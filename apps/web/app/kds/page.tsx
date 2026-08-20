"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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

type QCResult = {
  verdict: "PASS" | "MISMATCH" | "CHECK" | "UNREADABLE";
  missing: string[];
  unexpected: string[];
  issues: string[];
};

const QC_BADGE: Record<QCResult["verdict"], { label: string; cls: string }> = {
  PASS: { label: "✅ QC pass", cls: "bg-green-900/60 text-green-300" },
  MISMATCH: { label: "❌ Wrong dishes", cls: "bg-red-900/60 text-red-300" },
  CHECK: { label: "⚠️ Check before dispatch", cls: "bg-amber-900/60 text-amber-300" },
  UNREADABLE: { label: "🔄 Retake photo", cls: "bg-stone-600 text-stone-200" },
};

export default function Kds() {
  const [token, setToken] = useState<string | null>(null);
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [demoOtp, setDemoOtp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [orders, setOrders] = useState<Record<number, OrderEvent>>({});
  const [qc, setQc] = useState<Record<number, QCResult | "pending">>({});
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    setToken(localStorage.getItem("kds_token"));
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
      <main className="flex min-h-screen items-center justify-center bg-stone-900 text-stone-100">
        <div className="w-80 space-y-3 rounded-lg bg-stone-800 p-6">
          <h1 className="text-xl font-semibold">🥞 KDS Login</h1>
          <input
            className="w-full rounded bg-stone-700 px-3 py-2"
            placeholder="Staff phone"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
          {demoOtp === null ? (
            <button className="w-full rounded bg-amber-500 py-2 font-medium text-stone-900" onClick={requestOtp}>
              Send OTP
            </button>
          ) : (
            <>
              <p className="rounded bg-amber-200/20 px-2 py-1 text-xs text-amber-300">
                Demo OTP: <b>{demoOtp}</b>
              </p>
              <input
                className="w-full rounded bg-stone-700 px-3 py-2"
                placeholder="OTP"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
              />
              <button className="w-full rounded bg-amber-500 py-2 font-medium text-stone-900" onClick={verifyOtp}>
                Verify
              </button>
            </>
          )}
          {error && <p className="text-sm text-red-400">{error}</p>}
        </div>
      </main>
    );
  }

  const byStatus = (s: string) =>
    Object.values(orders)
      .filter((o) => o.status === s)
      .sort((a, b) => (a.placed_at ?? "").localeCompare(b.placed_at ?? ""));

  return (
    <main className="min-h-screen bg-stone-900 p-4 text-stone-100">
      <header className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold">🥞 DosaDash KDS</h1>
        <span className={`text-sm ${connected ? "text-green-400" : "text-red-400"}`}>
          {connected ? "● live" : "○ reconnecting…"}
        </span>
      </header>
      {error && <p className="mb-2 text-sm text-red-400">{error}</p>}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {COLUMNS.map((col) => (
          <section key={col} className="rounded-lg bg-stone-800 p-3">
            <h2 className="mb-2 text-sm font-bold tracking-wide text-amber-400">
              {col} ({byStatus(col).length})
            </h2>
            <div className="space-y-2">
              {byStatus(col).map((o) => (
                <article key={o.order_id} className="rounded bg-stone-700 p-2 text-sm">
                  <div className="flex justify-between font-semibold">
                    <span>
                      #{o.order_id}
                      {o.channel === "MOCK_AGGREGATOR" && (
                        <span className="ml-1 rounded bg-orange-900/70 px-1 text-[10px] text-orange-200" title="Order from an aggregator channel">
                          🛵 aggregator
                        </span>
                      )}
                      {o.channel === "TELEGRAM" && (
                        <span className="ml-1 rounded bg-sky-900/70 px-1 text-[10px] text-sky-200">✈️ telegram</span>
                      )}
                    </span>
                    <span>₹{o.total}</span>
                  </div>
                  <ul className="my-1 text-stone-300">
                    {o.items.map((i) => (
                      <li key={i.name}>
                        {i.qty}× {i.name}
                      </li>
                    ))}
                  </ul>
                  {qc[o.order_id] && qc[o.order_id] !== "pending" && (
                    <div
                      className={`mb-1 rounded px-2 py-1 text-xs ${
                        QC_BADGE[(qc[o.order_id] as QCResult).verdict].cls
                      }`}
                    >
                      <b>{QC_BADGE[(qc[o.order_id] as QCResult).verdict].label}</b>
                      {(qc[o.order_id] as QCResult).missing.length > 0 && (
                        <p>missing: {(qc[o.order_id] as QCResult).missing.join(", ")}</p>
                      )}
                      {(qc[o.order_id] as QCResult).issues.map((issue) => (
                        <p key={issue}>{issue}</p>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-1">
                    {(col === "COOKING" || col === "READY") && (
                      <label className="cursor-pointer rounded bg-stone-600 px-2 py-1 text-xs font-bold">
                        {qc[o.order_id] === "pending" ? "🔍…" : "📷 QC"}
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
                      className="flex-1 rounded bg-amber-500 py-1 text-xs font-bold text-stone-900"
                      onClick={() => advance(o)}
                    >
                      → {NEXT[o.status].replace(/_/g, " ")}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
    </main>
  );
}
