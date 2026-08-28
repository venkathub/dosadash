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
import FeedbackButton from "./components/FeedbackButton";
import Recommendations from "./components/Recommendations";
import CheckoutSuggestions from "./components/CheckoutSuggestions";
import { Btn, Card, FssaiMark, Input, PosterBlock, Ticker, Zari, cx } from "./components/ui";

type CartLine = { item: MenuItem; qty: number };

const SPICE = ["", "🌶", "🌶🌶", "🌶🌶🌶"];

// Grams of protein per serving at (or above) which a dish counts as
// "high protein". Tuned to this catalog: tiffin items (idli/dosa) sit around
// 4–10 g, while the curries, mess specials and paneer/egg dishes clear this
// line. Dishes with no approved estimate are never counted — an unknown is
// not a claim.
const HIGH_PROTEIN_G = 12;

type MealPeriod = "breakfast" | "lunch" | "snacks" | "dinner";

const HERO: Record<MealPeriod, { eyebrow: string; heading: string; art: string }> = {
  breakfast: { eyebrow: "Good morning · Chennai", heading: "Dosas are on the tawa ☀", art: "🥞" },
  lunch: { eyebrow: "Lunch hour · Chennai", heading: "Meals are steaming 🍛", art: "🍛" },
  snacks: { eyebrow: "Evening tiffin · Chennai", heading: "Bajjis & filter coffee 🫖", art: "☕" },
  dinner: { eyebrow: "Good evening · Chennai", heading: "Dinner is on the tawa 🔥", art: "🥞" },
};

const MEAL_PERIODS: MealPeriod[] = ["breakfast", "lunch", "snacks", "dinner"];

function currentMealPeriod(date = new Date()): MealPeriod {
  const h = date.getHours();
  if (h < 11) return "breakfast";
  if (h < 15) return "lunch";
  if (h < 18) return "snacks";
  return "dinner";
}

export default function Home() {
  const [menu, setMenu] = useState<MenuItem[]>([]);
  const [dietFilter, setDietFilter] = useState<"all" | "veg" | "nonveg">("all");
  const [highProtein, setHighProtein] = useState(false);
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

  // Show the chip only when at least one dish in the menu is actually scored at
  // or above the threshold — the filter must always return results when offered.
  const hasProteinData = useMemo(
    () => menu.some((m) => (m.protein_g ?? 0) >= HIGH_PROTEIN_G),
    [menu]
  );

  const visible = useMemo(
    () =>
      menu.filter(
        (m) =>
          (dietFilter === "all" || (dietFilter === "veg" ? m.is_veg : !m.is_veg)) &&
          (!highProtein || (m.protein_g ?? 0) >= HIGH_PROTEIN_G) &&
          (search.length < 2 ||
            m.name.toLowerCase().includes(search.toLowerCase()) ||
            (m.canonical_name ?? "").toLowerCase().includes(search.toLowerCase()))
      ),
    [menu, dietFilter, highProtein, search]
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
  // Cart lines hold a snapshot of the item from when it was added — resolve
  // against the freshly fetched menu so a dish that slipped out of its serving
  // window since then still blocks checkout (instead of a confusing 409).
  const offWindowLines = cartLines
    .map((l) => menu.find((m) => m.id === l.item.id) ?? l.item)
    .filter((m) => m.available_now === false);

  // Decorative ticker built from the live menu (never hardcode prices — drift).
  const tickerText = useMemo(() => {
    if (menu.length === 0) return "";
    const picks = menu.filter((m) => m.available_now !== false).slice(0, 5);
    const parts = picks.map((m) => `${m.name} ₹${parseFloat(m.price).toFixed(0)}`);
    return `  HOT OFF THE TAWA ✦ ${parts.join(" ✦ ")} ✦ HOT OFF THE TAWA ✦`;
  }, [menu]);

  const add = (item: MenuItem, delta: number) => {
    // Off-window dishes can never be ADDED (recs/suggestions funnel through
    // here too) — removing an existing line is always allowed.
    if (delta > 0 && item.available_now === false) return;
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
      <header className="sticky top-0 z-40 border-b-[3px] border-turmeric-500 bg-indigo-900 px-4 py-3">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-3">
          <h1 className="flex items-center gap-1.5 font-display text-lg font-bold tracking-wide text-white">
            🥞 DOSA<span className="-ml-1.5 text-turmeric-400">DASH</span>
          </h1>
          <Input
            tone="dark"
            className="w-40 rounded-full sm:w-64"
            placeholder="Search dishes…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm">
            <button
              className="flex overflow-hidden rounded-full border-2 border-indigo-600 font-display text-[11.5px] font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500"
              title={lang === "ta" ? "Switch to English" : "தமிழில் காட்டு"}
              onClick={() => switchLang(lang === "ta" ? "en" : "ta")}
            >
              <span
                className={cx(
                  "px-2 py-0.5",
                  lang === "en" ? "bg-turmeric-500 text-indigo-900" : "text-indigo-200",
                )}
              >
                EN
              </span>
              <span
                className={cx(
                  "px-2 py-0.5",
                  lang === "ta" ? "bg-turmeric-500 text-indigo-900" : "text-indigo-200",
                )}
              >
                தமிழ்
              </span>
            </button>
            <div
              role="group"
              aria-label="Diet filter"
              className="flex overflow-hidden rounded-full border-2 border-indigo-600 font-display text-[11.5px] font-bold"
            >
              {(["all", "veg", "nonveg"] as const).map((f) => (
                <button
                  key={f}
                  type="button"
                  aria-pressed={dietFilter === f}
                  className={cx(
                    "px-2.5 py-0.5 transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-magenta-500",
                    dietFilter === f
                      ? f === "veg"
                        ? "bg-veg text-white"
                        : "bg-turmeric-500 text-indigo-900"
                      : "text-indigo-200 hover:bg-indigo-700",
                  )}
                  onClick={() => setDietFilter(f)}
                >
                  {f === "all" ? "All" : f === "veg" ? "🌿 Veg" : "🍖 Non-veg"}
                </button>
              ))}
            </div>
            {hasProteinData && (
              <button
                type="button"
                aria-pressed={highProtein}
                title={`Show only dishes with ${HIGH_PROTEIN_G} g protein or more per serving`}
                className={cx(
                  "flex items-center gap-1.5 rounded-full border-2 px-2.5 py-0.5 text-xs font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500",
                  highProtein
                    ? "border-indigo-900 bg-turmeric-500 text-indigo-900"
                    : "border-indigo-600 text-indigo-100 hover:border-turmeric-400",
                )}
                onClick={() => setHighProtein((on) => !on)}
              >
                <span aria-hidden="true">💪</span>
                <span>High protein</span>
              </button>
            )}
            <Link
              href="/orders"
              className="text-indigo-100 underline decoration-turmeric-500/60 underline-offset-4 transition-colors duration-150 hover:text-turmeric-400"
            >
              Orders
            </Link>
            <Link
              href="/demo"
              className="text-indigo-100 underline decoration-turmeric-500/60 underline-offset-4 transition-colors duration-150 hover:text-turmeric-400"
              title="Demo guide: credentials + test cards"
            >
              Demo
            </Link>
            {user ? (
              <button
                className="text-indigo-200 underline underline-offset-4 transition-colors duration-150 hover:text-turmeric-400"
                onClick={() => {
                  clearSession();
                  setUser(null);
                }}
              >
                Logout
              </button>
            ) : (
              <Btn variant="turmeric" size="sm" onClick={() => setShowLogin(true)}>
                Login
              </Btn>
            )}
          </div>
        </div>
      </header>
      {tickerText && <Ticker>{tickerText}</Ticker>}

      {error && (
        <p className="mx-auto mt-3 max-w-4xl px-4 text-sm font-semibold text-chili">{error}</p>
      )}

      <div className="mx-auto max-w-4xl px-4">
        {menu.length > 0 && (
          <section className="relative mt-5 overflow-hidden rounded-2xl border-2 border-indigo-900 bg-magenta-500 px-5 py-5 text-white shadow-[5px_5px_0_#1B1B3A]">
            <div
              className="absolute -right-12 -top-12 h-40 w-40 rounded-full bg-magenta-400 opacity-50"
              aria-hidden="true"
            />
            <div
              className="absolute bottom-2 right-3 text-5xl [filter:drop-shadow(3px_3px_0_#1B1B3A)]"
              aria-hidden="true"
            >
              {HERO[period].art}
            </div>
            <div className="relative z-[1]">
              <p className="font-display text-[11px] font-bold uppercase tracking-[0.16em] text-turmeric-400">
                {HERO[period].eyebrow}
              </p>
              <p className="mt-1.5 font-display text-[27px] font-bold uppercase leading-[1.1] tracking-[0.01em]">
                {HERO[period].heading}
              </p>
              <div className="mt-3.5 flex flex-wrap gap-2">
                {MEAL_PERIODS.map((p) => (
                  <span
                    key={p}
                    className={cx(
                      "rounded-full border-2 px-2.5 py-0.5 text-[11.5px] font-semibold",
                      p === period
                        ? "border-indigo-900 bg-turmeric-500 text-indigo-900 shadow-pop-xs"
                        : "border-white bg-transparent text-white",
                    )}
                  >
                    {p === period ? `● ${p} — now serving` : p}
                  </span>
                ))}
              </div>
            </div>
          </section>
        )}
        <Recommendations
          cartIds={Object.keys(cart).map(Number)}
          menu={menu}
          onAdd={(item) => add(item, 1)}
        />
        {categories.map((cat, ci) => {
          const inCat = visible.filter((m) => m.category === cat);
          const label = inCat.find((m) => m.category_label)?.category_label ?? cat;
          return (
          <section key={cat} className="mt-8">
            <div className="mb-1 flex flex-wrap items-baseline gap-2.5">
              <PosterBlock tone={ci % 2 === 0 ? "magenta" : "indigo"} tamil={lang === "ta"}>
                {label}
              </PosterBlock>
              <span className="text-[11.5px] text-muted">{inCat.length} dishes</span>
            </div>
            <Zari className="mb-4" />
            <div className="grid gap-4 md:grid-cols-2">
              {inCat
                .sort((a, b) =>
                  highProtein
                    ? (b.protein_g ?? 0) - (a.protein_g ?? 0)
                    : Number(inPeriod(b)) - Number(inPeriod(a)),
                )
                .map((m) => {
                  const off = m.available_now === false;
                  return (
                  <div
                    key={m.id}
                    className={cx(
                      "flex justify-between gap-3 rounded-xl border-2 border-indigo-900 p-3",
                      off
                        ? "bg-sand-200"
                        : "bg-paper shadow-pop transition-transform duration-150 hover:-translate-y-0.5",
                    )}
                  >
                    {m.image_url && (
                      <div className="relative h-24 w-24 shrink-0">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={m.image_url}
                          alt={m.name}
                          className={cx(
                            "h-24 w-24 rounded-lg border-2 border-indigo-900 object-cover",
                            off && "opacity-40 saturate-50",
                          )}
                        />
                        {m.image_ai && (
                          <span
                            className="absolute -bottom-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-indigo-900 px-2 py-0.5 font-display text-[10px] font-bold tracking-[0.06em] text-turmeric-400"
                            title="This photo was generated by AI and approved by the kitchen"
                          >
                            ✨ AI
                          </span>
                        )}
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <h3 className="flex items-start gap-1.5">
                        <FssaiMark veg={m.is_veg} className="mt-[2px] shrink-0" />
                        <span
                          className={cx(
                            "break-words font-display text-[15px] font-bold leading-snug",
                            off ? "text-faint" : "text-ink",
                          )}
                        >
                          {m.name}
                        </span>
                      </h3>
                      <p
                        className={cx(
                          "mt-0.5 text-sm text-muted line-clamp-2",
                          off && "opacity-60",
                        )}
                      >
                        {m.description}
                      </p>
                      {(m.spice_level > 0 || m.allergens.length > 0 || m.protein_g != null) && (
                        <p className={cx("mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px] text-muted", off && "opacity-60")}>
                          {m.spice_level > 0 && (
                            <span className="tracking-widest text-chili">{SPICE[m.spice_level]}</span>
                          )}
                          {m.allergens.length > 0 && <span>· ⚠ {m.allergens.join(", ")}</span>}
                          {m.protein_g != null && (
                            <span
                              className={cx(
                                "tnum rounded-full border-[1.5px] border-indigo-900 px-1.5 py-0.5 text-[10.5px] font-semibold",
                                m.protein_g >= HIGH_PROTEIN_G
                                  ? "bg-turmeric-500 text-indigo-900"
                                  : "bg-sand-200 text-ink",
                              )}
                              title="Protein per serving — AI-estimated, approved by the kitchen"
                            >
                              💪 {m.protein_g.toFixed(0)} g protein
                            </span>
                          )}
                        </p>
                      )}
                      {/* Serving-window chip (server-built text, stays English by design) */}
                      {off ? (
                        <p className="mt-1.5">
                          <span className="inline-flex items-center gap-1 rounded-full border-[1.5px] border-turmeric-600 bg-warn-100 px-2 py-0.5 text-[11px] font-semibold text-[#8A6A03]">
                            ⏰ Not available now
                            {m.serving_windows && <> · Serves {m.serving_windows}</>}
                          </span>
                        </p>
                      ) : (
                        m.serving_windows && (
                          <p className="mt-1 text-[10px] text-faint">⏰ {m.serving_windows}</p>
                        )
                      )}
                      <p
                        className={cx(
                          "tnum mt-1 font-display text-base font-bold text-ink",
                          off && "opacity-50",
                        )}
                      >
                        ₹{m.price}
                      </p>
                    </div>
                    <div className="flex flex-col items-end justify-end">
                      {cart[m.id] ? (
                        <div className="flex items-center overflow-hidden rounded-lg border-2 border-indigo-900 bg-paper font-display font-bold shadow-pop-sm">
                          <button
                            className="h-8 w-8 bg-turmeric-500 text-indigo-900 transition-colors duration-150 hover:bg-turmeric-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500"
                            onClick={() => add(m, -1)}
                          >
                            −
                          </button>
                          <span className="tnum w-8 text-center text-sm text-ink">
                            {cart[m.id].qty}
                          </span>
                          <button
                            className="h-8 w-8 bg-turmeric-500 text-indigo-900 transition-colors duration-150 hover:bg-turmeric-400 disabled:cursor-not-allowed disabled:bg-sand-300 disabled:text-faint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500"
                            disabled={off}
                            onClick={() => add(m, 1)}
                          >
                            +
                          </button>
                        </div>
                      ) : (
                        <Btn
                          variant="turmeric"
                          size="sm"
                          disabled={off}
                          title={off ? "Not available right now" : undefined}
                          onClick={() => add(m, 1)}
                        >
                          ADD +
                        </Btn>
                      )}
                    </div>
                  </div>
                  );
                })}
            </div>
          </section>
          );
        })}
        {visible.length === 0 && (highProtein || dietFilter !== "all") && (
          <p className="mt-8 rounded-xl border-2 border-indigo-900 bg-sand-200 p-4 text-sm text-muted">
            {highProtein
              ? <>No dish on today&apos;s menu is scored at {HIGH_PROTEIN_G} g protein or more{dietFilter !== "all" && ` among the ${dietFilter} dishes`}. Switch the 💪 filter off to see everything.</>
              : <>No {dietFilter === "veg" ? "veg" : "non-veg"} dishes match your search. Switch the diet filter to <strong>All</strong> to see everything.</>}
          </p>
        )}
      </div>

      {cartLines.length > 0 && (
        <footer className="fixed bottom-0 left-0 right-0 z-40 border-t-[3px] border-turmeric-500 bg-indigo-900 p-3 text-indigo-100">
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
              variant="subtle"
              size="sm"
              disabled={couponCode.length < 2 || !!coupon}
              onClick={applyCoupon}
            >
              {coupon ? "✓ Applied" : "Apply"}
            </Btn>
            {coupon && (
              <span className="text-xs font-semibold text-[#5BD69B]">
                {coupon.code}: −₹{parseFloat(coupon.discount).toFixed(2)}
              </span>
            )}
            {couponError && (
              <span className="text-xs font-semibold text-[#FF8B8B]">{couponError}</span>
            )}
          </div>
          <div className="mx-auto flex max-w-4xl items-center justify-between gap-4">
            <p className="text-sm">
              <span className="text-[11.5px] text-indigo-200">
                {cartLines.reduce((s, l) => s + l.qty, 0)} items{coupon && " · coupon applied"}
              </span>
              <br />
              <span className="tnum font-display text-[21px] font-bold text-white">
                ₹{cartTotal.toFixed(2)}
              </span>{" "}
              {coupon && (
                <span className="text-sm font-semibold text-[#5BD69B]">
                  −₹{parseFloat(coupon.discount).toFixed(2)}
                </span>
              )}{" "}
              <span className="text-xs text-indigo-300">+ GST</span>
            </p>
            <Btn
              variant="magenta"
              size="lg"
              disabled={placing || offWindowLines.length > 0}
              title={
                offWindowLines.length > 0
                  ? "Remove the unavailable dishes to checkout"
                  : undefined
              }
              onClick={checkout}
            >
              {placing ? "Placing…" : "Checkout →"}
            </Btn>
          </div>
          {offWindowLines.length > 0 && (
            <div className="mx-auto mt-2 max-w-4xl space-y-1 text-xs">
              {offWindowLines.map((m) => (
                <p
                  key={m.id}
                  className="inline-block rounded-lg border-[1.5px] border-turmeric-600 bg-warn-100 px-2.5 py-1 font-semibold text-[#8A6A03]"
                >
                  ⏰ {m.name} is not available right now
                  {m.serving_windows && <> (serves {m.serving_windows})</>} — remove it to
                  checkout.
                </p>
              ))}
            </div>
          )}
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
      <FeedbackButton />
    </main>
  );
}
