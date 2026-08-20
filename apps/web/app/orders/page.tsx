"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api, getUser, type Order } from "../../lib/api";
import { ReviewBox } from "./reviewBox";
import { SupportBox } from "./supportBox";
import { Badge, Btn, Card, EmptyState, statusBadgeTone } from "../components/ui";

const lightGhostBtn =
  "rounded-lg border border-leaf-600 px-3 py-1 text-xs font-semibold text-leaf-800 transition-colors duration-150 hover:border-brass-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brass-500";

export default function Orders() {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tgLinked, setTgLinked] = useState<boolean | null>(null);
  const router = useRouter();

  const refreshMe = () =>
    api<{ tg_linked: boolean }>("/auth/me", { auth: true })
      .then((me) => setTgLinked(me.tg_linked))
      .catch(() => setTgLinked(null));

  useEffect(() => {
    if (!getUser()) {
      setError("Log in from the home page to see your orders.");
      setOrders([]);
      return;
    }
    api<Order[]>("/orders", { auth: true })
      .then(setOrders)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"));
    refreshMe();
  }, []);

  const linkTelegram = async () => {
    try {
      const r = await api<{ deep_link: string }>("/auth/telegram/link-code", {
        method: "POST",
        auth: true,
      });
      window.open(r.deep_link, "_blank");
      // poll a few times so the badge flips once the user taps START
      let tries = 0;
      const poll = setInterval(async () => {
        await refreshMe();
        if (++tries >= 12) clearInterval(poll);
      }, 5000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create link");
    }
  };

  const unlinkTelegram = async () => {
    try {
      await api("/auth/telegram/link", { method: "DELETE", auth: true });
      setTgLinked(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unlink failed");
    }
  };

  const reorder = async (id: number) => {
    setError(null);
    try {
      const fresh = await api<Order>(`/orders/${id}/reorder`, { method: "POST", auth: true });
      router.push(`/?track=${fresh.id}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setError(`Some items are sold out: ${e.message}`);
      else setError(e instanceof Error ? e.message : "Reorder failed");
    }
  };

  return (
    <main className="min-h-screen pb-10">
      <header className="sticky top-0 z-40 border-b border-brass-500/30 bg-leaf-800 px-4 py-3">
        <div className="mx-auto flex max-w-2xl flex-wrap items-center justify-between gap-3">
          <h1 className="font-display text-xl font-semibold tracking-tight text-brass-300">
            🥞 Your orders
          </h1>
          <div className="flex items-center gap-3">
            {tgLinked ? (
              <span className="flex items-center gap-2 text-xs">
                <Badge tone="info">✈️ Telegram linked ✓</Badge>
                <button
                  className="text-leaf-200 underline underline-offset-4 transition-colors duration-150 hover:text-brass-300"
                  onClick={unlinkTelegram}
                >
                  Unlink
                </button>
              </span>
            ) : (
              <Btn variant="leaf" size="sm" onClick={linkTelegram}>
                ✈️ Link Telegram
              </Btn>
            )}
            <Link
              href="/"
              className="text-sm text-leaf-100 underline decoration-brass-500/50 underline-offset-4 transition-colors duration-150 hover:text-brass-300"
            >
              ← Menu
            </Link>
          </div>
        </div>
      </header>
      <div className="mx-auto max-w-2xl px-4 py-6">
        {error && <p className="mb-3 text-sm text-chili-600">{error}</p>}
        {orders === null && <p className="text-ink-400">Loading…</p>}
        {orders?.length === 0 && !error && (
          <EmptyState surface="light">No orders yet — go grab a dosa!</EmptyState>
        )}
        <div className="space-y-3">
          {orders?.map((o) => (
            <Card key={o.id} tone="light" className="p-4">
              <div className="flex items-center justify-between">
                <h2 className="font-display text-lg font-semibold tracking-tight text-leaf-800">
                  #{o.id}
                </h2>
                <Badge surface="light" tone={statusBadgeTone(o.status)}>
                  {o.status}
                </Badge>
              </div>
              <p className="my-1 text-sm text-ink-600">
                {o.items.map((i) => `${i.qty}× ${i.name}`).join(", ")}
              </p>
              <div className="flex items-center justify-between">
                <p className="tnum text-sm text-ink-900">
                  <b className="font-display">₹{o.total}</b> ·{" "}
                  {new Date(o.placed_at).toLocaleString("en-IN")}
                </p>
                <div className="flex gap-2">
                  <button className={lightGhostBtn} onClick={() => router.push(`/?track=${o.id}`)}>
                    Track
                  </button>
                  <button className={lightGhostBtn} onClick={() => reorder(o.id)}>
                    ↻ Reorder
                  </button>
                </div>
              </div>
              {o.status === "DELIVERED" && <ReviewBox orderId={o.id} />}
            </Card>
          ))}
        </div>
        {getUser() && <SupportBox />}
      </div>
    </main>
  );
}
