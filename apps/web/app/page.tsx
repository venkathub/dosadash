"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  api,
  clearSession,
  getUser,
  type MenuItem,
  type Order,
  type User,
} from "../lib/api";
import LoginModal from "./components/LoginModal";
import OrderTracker from "./components/OrderTracker";
import ChatWidget from "./components/ChatWidget";

type CartLine = { item: MenuItem; qty: number };

const SPICE = ["", "🌶", "🌶🌶", "🌶🌶🌶"];

export default function Home() {
  const [menu, setMenu] = useState<MenuItem[]>([]);
  const [vegOnly, setVegOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [cart, setCart] = useState<Record<number, CartLine>>({});
  const [user, setUser] = useState<User | null>(null);
  const [showLogin, setShowLogin] = useState(false);
  const [tracking, setTracking] = useState<Order | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [placing, setPlacing] = useState(false);

  useEffect(() => {
    setUser(getUser());
    api<MenuItem[]>("/menu").then(setMenu).catch(() => setError("Menu failed to load"));
    const params = new URLSearchParams(location.search);
    const track = params.get("track");
    if (track) {
      api<Order>(`/orders/${track}`, { auth: true })
        .then(setTracking)
        .catch(() => null);
    }
  }, []);

  const visible = useMemo(
    () =>
      menu.filter(
        (m) =>
          (!vegOnly || m.is_veg) &&
          (search.length < 2 || m.name.toLowerCase().includes(search.toLowerCase()))
      ),
    [menu, vegOnly, search]
  );
  const categories = useMemo(() => [...new Set(visible.map((m) => m.category))], [visible]);
  const cartLines = Object.values(cart);
  const cartTotal = cartLines.reduce((s, l) => s + parseFloat(l.item.price) * l.qty, 0);

  const add = (item: MenuItem, delta: number) =>
    setCart((prev) => {
      const qty = (prev[item.id]?.qty ?? 0) + delta;
      const next = { ...prev };
      if (qty <= 0) delete next[item.id];
      else next[item.id] = { item, qty };
      return next;
    });

  const checkout = async () => {
    if (!user) return setShowLogin(true);
    setPlacing(true);
    setError(null);
    try {
      const order = await api<Order>("/orders", {
        method: "POST",
        auth: true,
        body: { items: cartLines.map((l) => ({ item_id: l.item.id, qty: l.qty })) },
      });
      setCart({});
      setTracking(order);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        clearSession();
        setUser(null);
        setShowLogin(true);
      } else setError(e instanceof Error ? e.message : "Checkout failed");
    } finally {
      setPlacing(false);
    }
  };

  return (
    <main className="min-h-screen pb-32">
      <header className="sticky top-0 z-40 border-b border-amber-200 bg-amber-50/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-3">
          <h1 className="text-xl font-extrabold">🥞 DosaDash</h1>
          <input
            className="w-40 rounded-full border border-stone-300 px-3 py-1 text-sm sm:w-64"
            placeholder="Search dishes…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="flex items-center gap-3 text-sm">
            <label className="flex cursor-pointer items-center gap-1">
              <input type="checkbox" checked={vegOnly} onChange={(e) => setVegOnly(e.target.checked)} />
              <span className="text-green-700">Veg</span>
            </label>
            <Link href="/orders" className="underline">
              Orders
            </Link>
            {user ? (
              <button
                className="text-stone-500 underline"
                onClick={() => {
                  clearSession();
                  setUser(null);
                }}
              >
                Logout
              </button>
            ) : (
              <button className="rounded bg-amber-500 px-3 py-1 font-semibold" onClick={() => setShowLogin(true)}>
                Login
              </button>
            )}
          </div>
        </div>
      </header>

      {error && <p className="mx-auto mt-3 max-w-4xl px-4 text-sm text-red-600">{error}</p>}

      <div className="mx-auto max-w-4xl px-4">
        {categories.map((cat) => (
          <section key={cat} className="mt-6">
            <h2 className="mb-2 text-lg font-bold">{cat}</h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {visible
                .filter((m) => m.category === cat)
                .map((m) => (
                  <article key={m.id} className="flex justify-between gap-2 rounded-lg border border-amber-200 bg-white p-3">
                    <div>
                      <h3 className="font-semibold">
                        <span className={m.is_veg ? "text-green-600" : "text-red-600"}>{m.is_veg ? "🟢" : "🔴"}</span>{" "}
                        {m.name} {SPICE[m.spice_level]}
                      </h3>
                      <p className="text-xs text-stone-500">{m.description}</p>
                      {m.allergens.length > 0 && (
                        <p className="mt-1 text-xs text-orange-600">⚠ {m.allergens.join(", ")}</p>
                      )}
                      <p className="mt-1 font-bold">₹{m.price}</p>
                    </div>
                    <div className="flex flex-col items-end justify-end">
                      {cart[m.id] ? (
                        <div className="flex items-center gap-2 rounded bg-amber-100 px-2 py-1">
                          <button className="font-bold" onClick={() => add(m, -1)}>
                            −
                          </button>
                          <span className="w-4 text-center text-sm font-semibold">{cart[m.id].qty}</span>
                          <button className="font-bold" onClick={() => add(m, 1)}>
                            +
                          </button>
                        </div>
                      ) : (
                        <button className="rounded bg-amber-500 px-4 py-1 text-sm font-bold" onClick={() => add(m, 1)}>
                          ADD
                        </button>
                      )}
                    </div>
                  </article>
                ))}
            </div>
          </section>
        ))}
      </div>

      {cartLines.length > 0 && (
        <footer className="fixed bottom-0 left-0 right-0 z-40 border-t border-amber-300 bg-white p-3 shadow-2xl">
          <div className="mx-auto flex max-w-4xl items-center justify-between gap-4">
            <p className="text-sm">
              <b>{cartLines.reduce((s, l) => s + l.qty, 0)} items</b> · ₹{cartTotal.toFixed(2)}{" "}
              <span className="text-stone-400">+ GST</span>
            </p>
            <button
              className="rounded-lg bg-green-600 px-6 py-2 font-bold text-white disabled:opacity-50"
              disabled={placing}
              onClick={checkout}
            >
              {placing ? "Placing…" : "Checkout →"}
            </button>
          </div>
        </footer>
      )}

      {showLogin && (
        <LoginModal
          onClose={() => setShowLogin(false)}
          onLogin={(u) => {
            setUser(u);
            setShowLogin(false);
          }}
        />
      )}
      {tracking && <OrderTracker order={tracking} onClose={() => setTracking(null)} />}
      <ChatWidget
        onRequireLogin={() => setShowLogin(true)}
        onPlaceOrder={async (items) => {
          const order = await api<Order>("/orders", { method: "POST", auth: true, body: { items } });
          setTracking(order);
        }}
      />
    </main>
  );
}
