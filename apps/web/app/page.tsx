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
import Recommendations from "./components/Recommendations";
import CheckoutSuggestions from "./components/CheckoutSuggestions";
import { Btn, Card, Input, SectionHeading, cx } from "./components/ui";

type CartLine = { item: MenuItem; qty: number };

const SPICE = ["", "🌶", "🌶🌶", "🌶🌶🌶"];

type MealPeriod = "breakfast" | "lunch" | "snacks" | "dinner";

const MEAL_GREETING: Record<MealPeriod, string> = {
  breakfast: "Good morning ☀ — dosas are on the tawa",
  lunch: "Lunch hour 🍛 — meals are steaming",
  snacks: "Evening tiffin 🫖 — bajjis & filter coffee",
  dinner: "Dinner time 🌙 — the tawa is still hot",
};

const MEAL_PERIODS: MealPeriod[] = ["breakfast", "lunch", "snacks", "dinner"];

function currentMealPeriod(date = new Date()): MealPeriod {
  const h = date.getHours();
  if (h < 11) return "breakfast";
  if (h < 15) return "lunch";
  if (h < 18) return "snacks";
  return "dinner";
}

/** FSSAI-style veg/non-veg mark: bordered square with a dot. */
function VegMark({ isVeg }: { isVeg: boolean }) {
  return (
    <span
      className={cx(
        "inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-[3px] border",
        isVeg ? "border-veg-500" : "border-chili-500",
      )}
      title={isVeg ? "Veg" : "Non-veg"}
    >
      <span
        className={cx(
          "h-1.5 w-1.5 rounded-full",
          isVeg ? "bg-veg-500" : "bg-chili-500",
        )}
      />
    </span>
  );
}

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
  const [couponCode, setCouponCode] = useState("");
  const [coupon, setCoupon] = useState<{ code: string; discount: string; total: string } | null>(null);
  const [couponError, setCouponError] = useState<string | null>(null);
  const [lang, setLang] = useState<"en" | "ta">("en");

  useEffect(() => {
    setUser(getUser());
    setLang(localStorage.getItem("menu_lang") === "ta" ? "ta" : "en");
    const params = new URLSearchParams(location.search);
    const track = params.get("track");
    if (track) {
      api<Order>(`/orders/${track}`, { auth: true })
        .then(setTracking)
        .catch(() => null);
    }
  }, []);

  // Owner-approved translations only; English is the canonical fallback.
  useEffect(() => {
    api<MenuItem[]>(lang === "ta" ? "/menu?lang=ta" : "/menu")
      .then(setMenu)
      .catch(() => setError("Menu failed to load"));
  }, [lang]);

  const switchLang = (next: "en" | "ta") => {
    localStorage.setItem("menu_lang", next);
    setLang(next);
  };

  const period = useMemo(() => currentMealPeriod(), []);
  const inPeriod = (m: MenuItem) =>
    m.meal_periods.length === 0 || m.meal_periods.includes(period);

  const visible = useMemo(
    () =>
      menu.filter(
        (m) =>
          (!vegOnly || m.is_veg) &&
          (search.length < 2 ||
            m.name.toLowerCase().includes(search.toLowerCase()) ||
            (m.canonical_name ?? "").toLowerCase().includes(search.toLowerCase()))
      ),
    [menu, vegOnly, search]
  );
  // Current meal period's categories first; within a category, matching items first.
  const categories = useMemo(() => {
    const matches = (cat: string) =>
      visible.some((m) => m.category === cat && m.meal_periods.includes(period));
    return [...new Set(visible.map((m) => m.category))].sort(
      (a, b) => Number(matches(b)) - Number(matches(a))
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, period]);
  const cartLines = Object.values(cart);
  const cartTotal = cartLines.reduce((s, l) => s + parseFloat(l.item.price) * l.qty, 0);

  const add = (item: MenuItem, delta: number) => {
    setCoupon(null); // cart changed — the preview no longer prices this cart
    setCouponError(null);
    setCart((prev) => {
      const qty = (prev[item.id]?.qty ?? 0) + delta;
      const next = { ...prev };
      if (qty <= 0) delete next[item.id];
      else next[item.id] = { item, qty };
      return next;
    });
  };

  const applyCoupon = async () => {
    if (!user) return setShowLogin(true);
    setCouponError(null);
    try {
      const preview = await api<{ code: string; discount: string; total: string }>(
        "/coupons/preview",
        {
          method: "POST",
          auth: true,
          body: {
            code: couponCode,
            items: cartLines.map((l) => ({ item_id: l.item.id, qty: l.qty })),
          },
        }
      );
      setCoupon(preview);
    } catch (e) {
      setCoupon(null);
      setCouponError(e instanceof Error ? e.message : "Coupon failed");
    }
  };

  const checkout = async () => {
    if (!user) return setShowLogin(true);
    setPlacing(true);
    setError(null);
    try {
      const order = await api<Order>("/orders", {
        method: "POST",
        auth: true,
        body: {
          items: cartLines.map((l) => ({ item_id: l.item.id, qty: l.qty })),
          coupon_code: coupon?.code ?? null,
        },
      });
      setCart({});
      setCoupon(null);
      setCouponCode("");
      setTracking(order);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        clearSession();
        setUser(null);
        setShowLogin(true);
      } else if (e instanceof ApiError && e.status === 400) {
        // coupon rejected at checkout (limits/expiry changed since preview)
        setCoupon(null);
        setCouponError(e.message);
      } else setError(e instanceof Error ? e.message : "Checkout failed");
    } finally {
      setPlacing(false);
    }
  };

  return (
    <main className="min-h-screen pb-32">
      <header className="sticky top-0 z-40 border-b border-brass-500/30 bg-leaf-800/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-3">
          <h1 className="font-display text-xl font-semibold tracking-tight text-brass-300">
            🥞 DosaDash
          </h1>
          <Input
            tone="light"
            className="w-40 rounded-full sm:w-64"
            placeholder="Search dishes…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="flex items-center gap-3 text-sm">
            <button
              className="rounded-full border border-cream-300 bg-cream-50 px-2.5 py-0.5 text-xs font-semibold text-leaf-800 transition-colors duration-150 hover:border-brass-500 hover:bg-cream-200"
              title={lang === "ta" ? "Switch to English" : "தமிழில் காட்டு"}
              onClick={() => switchLang(lang === "ta" ? "en" : "ta")}
            >
              {lang === "ta" ? "EN" : "தமிழ்"}
            </button>
            <label
              className={cx(
                "flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors duration-150",
                vegOnly
                  ? "border-veg-500 bg-cream-50 text-veg-600"
                  : "border-leaf-600 text-leaf-100 hover:border-brass-400",
              )}
            >
              <input
                type="checkbox"
                className="accent-[#2F8A56]"
                checked={vegOnly}
                onChange={(e) => setVegOnly(e.target.checked)}
              />
              <span>Veg</span>
            </label>
            <Link
              href="/orders"
              className="text-leaf-100 underline decoration-brass-500/50 underline-offset-4 transition-colors duration-150 hover:text-brass-300"
            >
              Orders
            </Link>
            <Link
              href="/demo"
              className="text-leaf-100 underline decoration-brass-500/50 underline-offset-4 transition-colors duration-150 hover:text-brass-300"
              title="Demo guide: credentials + test cards"
            >
              Demo
            </Link>
            {user ? (
              <button
                className="text-leaf-200 underline underline-offset-4 transition-colors duration-150 hover:text-brass-300"
                onClick={() => {
                  clearSession();
                  setUser(null);
                }}
              >
                Logout
              </button>
            ) : (
              <Btn size="sm" onClick={() => setShowLogin(true)}>
                Login
              </Btn>
            )}
          </div>
        </div>
      </header>

      {error && <p className="mx-auto mt-3 max-w-4xl px-4 text-sm text-chili-600">{error}</p>}

      <div className="mx-auto max-w-4xl px-4">
        {menu.length > 0 && (
          <section className="mt-4 rounded-2xl bg-gradient-to-br from-leaf-800 to-leaf-700 px-5 py-4 shadow-card">
            <p className="font-display text-lg font-semibold tracking-tight text-leaf-100">
              {MEAL_GREETING[period]}
            </p>
            <p className="mt-0.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-brass-300/80">
              Good for {period} right now
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {MEAL_PERIODS.map((p) => (
                <span
                  key={p}
                  className={cx(
                    "rounded-full px-3 py-1 text-xs font-semibold",
                    p === period
                      ? "btn-gold shadow-card"
                      : "bg-leaf-700 text-leaf-200",
                  )}
                >
                  {p}
                </span>
              ))}
            </div>
          </section>
        )}
        <Recommendations
          cartIds={Object.keys(cart).map(Number)}
          menu={menu}
          onAdd={(item) => add(item, 1)}
        />
        {categories.map((cat) => (
          <section key={cat} className="mt-8">
            <SectionHeading as="h2" className="mb-4 text-2xl text-leaf-800">
              {visible.find((m) => m.category === cat && m.category_label)?.category_label ?? cat}
            </SectionHeading>
            <div className="grid gap-4 md:grid-cols-2">
              {visible
                .filter((m) => m.category === cat)
                .sort((a, b) => Number(inPeriod(b)) - Number(inPeriod(a)))
                .map((m) => (
                  <Card
                    key={m.id}
                    tone="light"
                    hover
                    className="flex justify-between gap-3 p-3"
                  >
                    {m.image_url && (
                      <div className="relative h-24 w-24 shrink-0">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={m.image_url}
                          alt={m.name}
                          className="h-24 w-24 rounded-lg object-cover"
                        />
                        {m.image_ai && (
                          <span
                            className="absolute bottom-0 right-0 rounded-tl-lg rounded-br-lg bg-leaf-950/80 px-1.5 py-0.5 text-[9px] font-semibold text-brass-300"
                            title="This photo was generated by AI and approved by the kitchen"
                          >
                            ✨ AI
                          </span>
                        )}
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <h3 className="flex items-center gap-1.5 font-semibold text-ink-900">
                        <VegMark isVeg={m.is_veg} />
                        <span className="truncate">{m.name}</span>
                      </h3>
                      <p className="mt-0.5 text-sm text-ink-600 line-clamp-2">{m.description}</p>
                      {(m.spice_level > 0 || m.allergens.length > 0) && (
                        <p className="mt-1 flex flex-wrap gap-1">
                          {m.spice_level > 0 && (
                            <span className="rounded-full border border-chili-500/30 bg-chili-200/50 px-1.5 py-0.5 text-[10px]">
                              {SPICE[m.spice_level]}
                            </span>
                          )}
                          {m.allergens.length > 0 && (
                            <span className="rounded-full border border-turmeric-500/40 bg-turmeric-200 px-1.5 py-0.5 text-[10px] text-ink-900">
                              ⚠ {m.allergens.join(", ")}
                            </span>
                          )}
                        </p>
                      )}
                      {m.meal_periods.length > 0 && (
                        <p className="mt-1 flex flex-wrap gap-1">
                          {m.meal_periods.map((p) => (
                            <span
                              key={p}
                              className={cx(
                                "rounded-full px-1.5 py-0.5 text-[10px]",
                                p === period
                                  ? "bg-brass-300/40 font-semibold text-brass-600"
                                  : "bg-cream-200 text-ink-400",
                              )}
                            >
                              {p}
                            </span>
                          ))}
                        </p>
                      )}
                      <p className="tnum mt-1 font-display text-base font-semibold text-leaf-800">
                        ₹{m.price}
                      </p>
                    </div>
                    <div className="flex flex-col items-end justify-end">
                      {cart[m.id] ? (
                        <div className="flex items-center gap-1 rounded-full border border-leaf-600 px-1 py-0.5">
                          <button
                            className="h-6 w-6 rounded-full font-bold text-leaf-800 transition-colors duration-150 hover:bg-cream-200"
                            onClick={() => add(m, -1)}
                          >
                            −
                          </button>
                          <span className="tnum w-5 text-center text-sm font-semibold text-ink-900">
                            {cart[m.id].qty}
                          </span>
                          <button
                            className="h-6 w-6 rounded-full font-bold text-leaf-800 transition-colors duration-150 hover:bg-cream-200"
                            onClick={() => add(m, 1)}
                          >
                            +
                          </button>
                        </div>
                      ) : (
                        <Btn size="sm" onClick={() => add(m, 1)}>
                          ADD
                        </Btn>
                      )}
                    </div>
                  </Card>
                ))}
            </div>
          </section>
        ))}
      </div>

      {cartLines.length > 0 && (
        <footer className="fixed bottom-0 left-0 right-0 z-40 rounded-t-2xl border-t border-brass-500/30 bg-leaf-900 p-3 shadow-modal">
          <CheckoutSuggestions
            cartIds={Object.keys(cart).map(Number)}
            menu={menu}
            onAdd={(item) => add(item, 1)}
          />
          <div className="mx-auto mb-2 flex max-w-4xl flex-wrap items-center gap-2 text-sm">
            <Input
              tone="dark"
              className="w-32 px-2 py-1 text-xs uppercase"
              placeholder="Coupon code"
              value={couponCode}
              onChange={(e) => {
                setCouponCode(e.target.value.toUpperCase());
                setCoupon(null);
                setCouponError(null);
              }}
            />
            <Btn
              variant="leaf"
              size="sm"
              disabled={couponCode.length < 2 || !!coupon}
              onClick={applyCoupon}
            >
              {coupon ? "✓ Applied" : "Apply"}
            </Btn>
            {coupon && (
              <span className="text-xs font-semibold text-veg-200">
                {coupon.code}: −₹{parseFloat(coupon.discount).toFixed(2)}
              </span>
            )}
            {couponError && <span className="text-xs text-chili-200">{couponError}</span>}
          </div>
          <div className="mx-auto flex max-w-4xl items-center justify-between gap-4">
            <p className="text-sm text-leaf-100">
              <b>{cartLines.reduce((s, l) => s + l.qty, 0)} items</b> ·{" "}
              <span className="tnum font-display text-base font-semibold text-cream-50">
                ₹{cartTotal.toFixed(2)}
              </span>{" "}
              {coupon && (
                <span className="font-semibold text-veg-200">
                  −₹{parseFloat(coupon.discount).toFixed(2)}
                </span>
              )}{" "}
              <span className="text-leaf-500">+ GST</span>
            </p>
            <Btn size="lg" disabled={placing} onClick={checkout}>
              {placing ? "Placing…" : "Checkout →"}
            </Btn>
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
