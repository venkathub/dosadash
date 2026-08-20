"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api, getUser, type Order } from "../../lib/api";
import { ReviewBox } from "./reviewBox";
import { SupportBox } from "./supportBox";

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
    <main className="mx-auto min-h-screen max-w-2xl px-4 py-6">
      <header className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-extrabold">🥞 Your orders</h1>
        <div className="flex items-center gap-3">
          {tgLinked ? (
            <span className="flex items-center gap-2 text-xs">
              <span className="rounded bg-sky-100 px-2 py-1 font-semibold text-sky-700">
                ✈️ Telegram linked ✓
              </span>
              <button className="text-stone-500 underline" onClick={unlinkTelegram}>
                Unlink
              </button>
            </span>
          ) : (
            <button
              className="rounded bg-sky-500 px-3 py-1 text-xs font-bold text-white"
              onClick={linkTelegram}
            >
              ✈️ Link Telegram
            </button>
          )}
          <Link href="/" className="text-sm underline">
            ← Menu
          </Link>
        </div>
      </header>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      {orders === null && <p className="text-stone-500">Loading…</p>}
      {orders?.length === 0 && !error && <p className="text-stone-500">No orders yet — go grab a dosa!</p>}
      <div className="space-y-3">
        {orders?.map((o) => (
          <article key={o.id} className="rounded-lg border border-amber-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <h2 className="font-bold">#{o.id}</h2>
              <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold">{o.status}</span>
            </div>
            <p className="my-1 text-sm text-stone-600">
              {o.items.map((i) => `${i.qty}× ${i.name}`).join(", ")}
            </p>
            <div className="flex items-center justify-between">
              <p className="text-sm">
                <b>₹{o.total}</b> · {new Date(o.placed_at).toLocaleString("en-IN")}
              </p>
              <div className="flex gap-2">
                <button
                  className="rounded border border-amber-400 px-3 py-1 text-xs font-semibold"
                  onClick={() => router.push(`/?track=${o.id}`)}
                >
                  Track
                </button>
                <button
                  className="rounded bg-amber-500 px-3 py-1 text-xs font-bold"
                  onClick={() => reorder(o.id)}
                >
                  ↻ Reorder
                </button>
              </div>
            </div>
            {o.status === "DELIVERED" && <ReviewBox orderId={o.id} />}
          </article>
        ))}
      </div>
      {getUser() && <SupportBox />}
    </main>
  );
}
