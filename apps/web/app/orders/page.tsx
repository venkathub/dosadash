"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api, getUser, type Order } from "../../lib/api";
import { ReviewBox } from "./reviewBox";
import { SupportBox } from "./supportBox";
import { Badge, Btn, Card, EmptyState, statusBadgeTone } from "../components/ui";

const lightGhostBtn =
  "rounded-lg border-2 border-indigo-900 bg-paper px-3 py-1 font-display text-xs font-bold text-ink shadow-pop-xs transition-colors duration-150 hover:bg-turmeric-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500";

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
      <header className="sticky top-0 z-40 border-b-[3px] border-turmeric-500 bg-indigo-900 px-4 py-3">
        <div className="mx-auto flex max-w-2xl flex-wrap items-center justify-between gap-3">
          <h1 className="font-display text-xl font-bold tracking-tight text-white">
            🥞 Your orders
          </h1>
          <div className="flex items-center gap-3">
            {tgLinked ? (
              <span className="flex items-center gap-2 text-xs">
                <Badge tone="info">✈️ Telegram linked ✓</Badge>
                <button
                  className="text-indigo-200 underline underline-offset-4 transition-colors duration-150 hover:text-turmeric-400"
                  onClick={unlinkTelegram}
                >
                  Unlink
                </button>
              </span>
            ) : (
              <Btn variant="turmeric" size="sm" onClick={linkTelegram}>
                ✈️ Link Telegram
              </Btn>
            )}
            <Link
              href="/"
              className="text-sm text-indigo-100 underline decoration-turmeric-500/60 underline-offset-4 transition-colors duration-150 hover:text-turmeric-400"
            >
              ← Menu
            </Link>
          </div>
        </div>
      </header>
      <div className="mx-auto max-w-2xl px-4 py-6">
        {error && <p className="mb-3 text-sm font-semibold text-chili">{error}</p>}
        {orders === null && <p className="text-faint">Loading…</p>}
        {orders?.length === 0 && !error && (
          <EmptyState surface="light">No orders yet — go grab a dosa!</EmptyState>
        )}
        <div className="space-y-4">
          {orders?.map((o) => (
            <Card key={o.id} tone="light" className="p-4">
              <div className="flex items-center justify-between">
                <h2 className="font-display text-lg font-bold tracking-tight text-ink">
                  #{o.id}
                </h2>
                <Badge surface="light" tone={statusBadgeTone(o.status)}>
                  {o.status}
                </Badge>
              </div>
              <p className="my-1 text-sm text-muted">
                {o.items.map((i) => `${i.qty}× ${i.name}`).join(", ")}
              </p>
              <div className="flex items-center justify-between">
                <p className="tnum text-sm text-ink">
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
