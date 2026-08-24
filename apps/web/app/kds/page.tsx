"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Badge,
  Btn,
  Card,
  ErrorBar,
  Input,
  SectionHeading,
  cx,
  statusBadgeTone,
} from "../components/ui";

type OrderEvent = {
  type: string;
  order_id: number;
  status: string;
  total: string;
  channel: string;
  placed_at: string | null;
  items: { name: string; qty: number }[];
};

const COLUMNS = ["PLACED", "CONFIRMED", "COOKING", "READY"] as const;
const NEXT: Record<string, string> = {
  PLACED: "CONFIRMED",
  CONFIRMED: "COOKING",
  COOKING: "READY",
  READY: "OUT_FOR_DELIVERY",
};

/** 6px left accent bar per column — readable from across the kitchen.
 *  (Madras Pop: PLACED sky / CONFIRMED magenta / COOKING turmeric / READY veg) */
const STATUS_ACCENT: Record<(typeof COLUMNS)[number], string> = {
  PLACED: "bg-sky",
  CONFIRMED: "bg-magenta-500",
  COOKING: "bg-turmeric-500",
  READY: "bg-veg",
};

/** An order this old is running late — the timer flips chili. */
const LATE_MINUTES = 20;

type QCResult = {
  verdict: "PASS" | "MISMATCH" | "CHECK" | "UNREADABLE";
  missing: string[];
  unexpected: string[];
  issues: string[];
};

const QC_LABEL: Record<QCResult["verdict"], string> = {
  PASS: "✅ QC pass",
  MISMATCH: "❌ Wrong dishes",
  CHECK: "⚠️ Check before dispatch",
  UNREADABLE: "🔄 Retake photo",
};

function elapsedMinutes(placedAt: string | null, now: number): number | null {
  if (!placedAt) return null;
  const t = Date.parse(placedAt);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.floor((now - t) / 60_000));
}

export default function Kds() {
  const [token, setToken] = useState<string | null>(null);
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [demoOtp, setDemoOtp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [orders, setOrders] = useState<Record<number, OrderEvent>>({});
  const [qc, setQc] = useState<Record<number, QCResult | "pending">>({});
  const [connected, setConnected] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    setToken(localStorage.getItem("kds_token"));
  }, []);

  // Tick the elapsed timers twice a minute (cheap-tablet friendly — no rAF).
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const requestOtp = async () => {
    setError(null);
    const r = await fetch("/api/v1/auth/otp/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone }),
    });
    const body = await r.json();
    if (!r.ok) return setError(body.detail ?? "OTP request failed");
    setDemoOtp(body.demo_otp);
  };

  const verifyOtp = async () => {
    setError(null);
    const r = await fetch("/api/v1/auth/otp/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone, otp }),
    });
    const body = await r.json();
    if (!r.ok) return setError(body.detail ?? "verification failed");
    if (body.user.role !== "kitchen_staff" && body.user.role !== "admin" && body.user.role !== "owner") {
      return setError(`This account has role '${body.user.role}' — KDS needs kitchen staff access.`);
    }
    localStorage.setItem("kds_token", body.access_token);
    setToken(body.access_token);
  };

  const connect = useCallback(() => {
    if (!token) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/kds?token=${token}`);
    wsRef.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = (e) => {
      setConnected(false);
      if (e.code === 4401 || e.code === 4403) {
        localStorage.removeItem("kds_token");
        setToken(null);
        setError(e.code === 4403 ? "Staff access required" : "Session expired — log in again");
      } else {
        setTimeout(connect, 2000); // auto-reconnect
      }
    };
    ws.onmessage = (msg) => {
      const data = JSON.parse(msg.data);
      if (data.type === "snapshot") {
        const map: Record<number, OrderEvent> = {};
        for (const o of data.orders) map[o.order_id] = o;
        setOrders(map);
      } else {
        setOrders((prev) => ({ ...prev, [data.order_id]: data }));
      }
    };
  }, [token]);

  useEffect(() => {
    if (token) connect();
    return () => wsRef.current?.close(1000);
  }, [token, connect]);

  const advance = async (o: OrderEvent) => {
    const r = await fetch(`/api/v1/orders/${o.order_id}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ status: NEXT[o.status] }),
    });
    if (!r.ok) setError((await r.json()).detail ?? "transition failed");
  };

  const qcPhoto = async (orderId: number, file: File) => {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setError("QC photo must be JPEG/PNG/WebP");
      return;
    }
    setQc((prev) => ({ ...prev, [orderId]: "pending" }));
    try {
      const b64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve((reader.result as string).split(",")[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const r = await fetch(`/api/v1/orders/${orderId}/qc-photo`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ image_base64: b64, mime_type: file.type }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? "QC failed");
      const result: QCResult = await r.json();
      setQc((prev) => ({ ...prev, [orderId]: result }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "QC failed");
      setQc((prev) => {
        const next = { ...prev };
        delete next[orderId];
        return next;
      });
    }
  };

  if (!token) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-indigo-950 text-indigo-100">
        <Card tone="dark" className="w-80 space-y-3 p-6">
          <SectionHeading as="h1" className="text-xl text-white">
            🥞 Kitchen sign-in
          </SectionHeading>
          <Input
            tone="dark"
            className="min-h-[44px] w-full"
            placeholder="Staff phone"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
          {demoOtp === null ? (
            <Btn variant="turmeric" className="min-h-[44px] w-full" onClick={requestOtp}>
              Send OTP
            </Btn>
          ) : (
            <>
              <p className="rounded-lg border-[1.5px] border-turmeric-600 bg-turmeric-500/15 px-2 py-1 text-xs text-turmeric-400">
                Demo OTP: <b className="font-display">{demoOtp}</b>
              </p>
              <Input
                tone="dark"
                className="min-h-[44px] w-full"
                placeholder="OTP"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
              />
              <Btn variant="turmeric" className="min-h-[44px] w-full" onClick={verifyOtp}>
                Verify
              </Btn>
            </>
          )}
          <ErrorBar msg={error} />
        </Card>
      </main>
    );
  }

  const byStatus = (s: string) =>
    Object.values(orders)
      .filter((o) => o.status === s)
      .sort((a, b) => (a.placed_at ?? "").localeCompare(b.placed_at ?? ""));

  return (
    <main className="min-h-screen bg-indigo-950 text-indigo-100">
      <header className="mb-4 flex items-center gap-4 border-b-[3px] border-turmeric-500 bg-indigo-900 px-5 py-3">
        <h1 className="font-display text-2xl font-bold uppercase tracking-wide text-white">
          🔥 Kitchen
        </h1>
        {connected ? (
          <span className="font-display text-[13px] font-bold uppercase tracking-[0.1em] text-[#5BD69B]">
            <span className="animate-pulse-soft">●</span> live
          </span>
        ) : (
          <span className="font-display text-[13px] font-bold uppercase tracking-[0.1em] text-turmeric-400">
            ○ reconnecting…
          </span>
        )}
      </header>
      {error && (
        <div className="mx-4 mb-2">
          <ErrorBar msg={error} />
        </div>
      )}
      <div className="grid grid-cols-2 items-start gap-3.5 p-4 pt-0 lg:grid-cols-4">
        {COLUMNS.map((col) => (
          <section
            key={col}
            className="rounded-xl border-2 border-indigo-700 bg-indigo-900 p-3"
          >
            <div className="mb-3 flex items-center justify-between px-1">
              <span className="font-display text-[11px] font-bold uppercase tracking-[0.16em] text-turmeric-400">
                {col}
              </span>
              <span className="tnum min-w-[34px] rounded-full bg-turmeric-500 px-3 text-center font-display text-[15px] font-bold text-indigo-900">
                {byStatus(col).length}
              </span>
            </div>
            <div className="space-y-3">
              {byStatus(col).map((o) => {
                const mins = elapsedMinutes(o.placed_at, now);
                const late = mins !== null && mins >= LATE_MINUTES;
                return (
                <article
                  key={o.order_id}
                  className="animate-fade-up relative overflow-hidden rounded-lg border-2 border-indigo-600 bg-indigo-800 p-3 pl-4 text-sm"
                >
                  <span
                    aria-hidden
                    className={`absolute left-0 top-0 h-full w-1.5 ${STATUS_ACCENT[col]}`}
                  />
                  <div className="flex items-center justify-between gap-1.5">
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="font-display text-lg font-bold text-white">
                        #{o.order_id}
                      </span>
                      {o.channel === "MOCK_AGGREGATOR" && (
                        <Badge tone="warning" title="Order from an aggregator channel">
                          🛵 aggregator
                        </Badge>
                      )}
                      {o.channel === "TELEGRAM" && <Badge tone="info">✈️ telegram</Badge>}
                    </span>
                    {mins !== null && (
                      <span
                        className={cx(
                          "tnum rounded-full border-[1.5px] px-2.5 py-0.5 font-display text-[13px] font-bold",
                          late
                            ? "border-chili bg-chili/20 text-[#FF8B8B]"
                            : "border-indigo-600 bg-indigo-950 text-indigo-100",
                        )}
                        title={late ? `running late (≥${LATE_MINUTES} min)` : "minutes since placed"}
                      >
                        {mins}m
                      </span>
                    )}
                  </div>
                  <ul className="my-1.5 text-[15px] font-bold leading-snug text-white">
                    {o.items.map((i) => (
                      <li key={i.name}>
                        {i.qty}× {i.name}
                      </li>
                    ))}
                  </ul>
                  <p className="tnum mb-2 font-display text-base font-bold text-turmeric-400">
                    ₹{o.total}
                  </p>
                  {qc[o.order_id] && qc[o.order_id] !== "pending" && (
                    <div className="mb-1.5 space-y-0.5">
                      <Badge tone={statusBadgeTone((qc[o.order_id] as QCResult).verdict)}>
                        {QC_LABEL[(qc[o.order_id] as QCResult).verdict]}
                      </Badge>
                      {(qc[o.order_id] as QCResult).missing.length > 0 && (
                        <p className="text-xs font-semibold text-[#FF8B8B]">
                          missing: {(qc[o.order_id] as QCResult).missing.join(", ")}
                        </p>
                      )}
                      {(qc[o.order_id] as QCResult).issues.map((issue) => (
                        <p key={issue} className="text-xs text-turmeric-400">
                          {issue}
                        </p>
                      ))}
                    </div>
                  )}
                  <div className="flex flex-col gap-1.5">
                    {(col === "COOKING" || col === "READY") && (
                      <label
                        className={`inline-flex min-h-[44px] cursor-pointer items-center justify-center gap-1.5 rounded-lg border-2 border-indigo-600 bg-transparent px-2.5 py-1 font-display text-sm font-bold text-indigo-100 transition-colors duration-150 hover:border-turmeric-400 hover:text-turmeric-400 ${
                          qc[o.order_id] === "pending" ? "cursor-not-allowed opacity-40" : ""
                        }`}
                      >
                        {qc[o.order_id] === "pending" ? "🔍…" : "📷 QC photo"}
                        <input
                          type="file"
                          accept="image/jpeg,image/png,image/webp"
                          capture="environment"
                          className="hidden"
                          disabled={qc[o.order_id] === "pending"}
                          onChange={(e) => {
                            const file = e.target.files?.[0];
                            if (file) qcPhoto(o.order_id, file);
                            e.target.value = "";
                          }}
                        />
                      </label>
                    )}
                    <button
                      className="flex min-h-[44px] w-full items-center justify-center gap-2 rounded-lg border-2 border-indigo-950 bg-turmeric-500 font-display text-base font-bold text-indigo-900 shadow-pop-dark-sm transition-colors duration-100 hover:bg-turmeric-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500"
                      onClick={() => advance(o)}
                    >
                      → {NEXT[o.status].replace(/_/g, " ")}
                    </button>
                  </div>
                </article>
                );
              })}
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
