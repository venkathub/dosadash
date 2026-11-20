"use client";

// Customer home — "Tawa Noir" (docs/17-ui-tawa-noir-design.md).
// Ordering comes first: search, category tabs and the menu are on the first
// screen and the cart is always in view. The 3D scene is a compact hero banner.
// Presentation only: cart, coupon, checkout, serving-window guard, language
// and filter logic are carried over unchanged from the Madras Pop page.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  api,
  SESSION_EXPIRED_EVENT,
  clearSession,
  getUser,
  type MenuItem,
  type Order,
  type User,
} from "../lib/api";
import { rupee } from "../lib/format";
import { isSearching, searchMenu } from "../lib/menuSearch";
import { cutoutKeyFor } from "../lib/scene";
import LoginModal from "./components/LoginModal";
import OrderTracker from "./components/OrderTracker";
import ChatWidget from "./components/ChatWidget";
import FeedbackButton from "./components/FeedbackButton";
import Recommendations from "./components/Recommendations";
import CheckoutSuggestions from "./components/CheckoutSuggestions";
import Scene3D, { type Scene3DHandle } from "./components/Scene3D";
import { cx } from "./components/ui";

type CartLine = { item: MenuItem; qty: number };

const SPICE = ["", "🌶", "🌶🌶", "🌶🌶🌶"];

// Grams of protein per serving at (or above) which a dish counts as
// "high protein". Tuned to this catalog: tiffin items (idli/dosa) sit around
// 4–10 g, while the curries, mess specials and paneer/egg dishes clear this
// line. Dishes with no approved estimate are never counted — an unknown is
// not a claim.
const HIGH_PROTEIN_G = 12;

type MealPeriod = "breakfast" | "lunch" | "snacks" | "dinner";

const GREETING: Record<MealPeriod, string> = {
  breakfast: "Good morning · Chennai",
  lunch: "Lunch hour · Chennai",
  snacks: "Evening tiffin · Chennai",
  dinner: "Good evening · Chennai",
};

function currentMealPeriod(date = new Date()): MealPeriod {
  const h = date.getHours();
  if (h < 11) return "breakfast";
  if (h < 15) return "lunch";
  if (h < 18) return "snacks";
  return "dinner";
}

const englishName = (m: MenuItem) => m.canonical_name ?? m.name;

/* ------------------------------ small pieces ------------------------------ */

const emberBtn =
  "inline-flex items-center justify-center rounded-full bg-ember font-display font-bold text-ember-ink transition duration-150 hover:bg-ember-hot active:translate-y-px disabled:cursor-not-allowed disabled:bg-soot-700 disabled:text-cream-faint disabled:shadow-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ghee focus-visible:ring-offset-2 focus-visible:ring-offset-soot-950";
const ghostBtn =
  "rounded-full border border-cream/15 px-3.5 py-1.5 text-[13px] text-cream-dim transition-colors duration-150 hover:border-ghee hover:text-cream focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ghee";
const eyebrow = "font-display text-xs font-semibold uppercase tracking-[0.2em] text-ghee";
const chip = "rounded-full border border-cream/15 px-2.5 py-0.5 text-xs text-cream-dim";

function VegMark({ veg }: { veg: boolean }) {
  return (
    <span
      role="img"
      aria-label={veg ? "Vegetarian" : "Non-vegetarian"}
      className={cx(
        "mt-[3px] grid h-4 w-4 shrink-0 place-items-center rounded-[3px] border-2",
        veg ? "border-basil" : "border-[#FF5D5D]",
      )}
    >
      <span className={cx("h-[7px] w-[7px] rounded-full", veg ? "bg-basil" : "bg-[#FF5D5D]")} />
    </span>
  );
}

/** ADD button that becomes a − qty + stepper once the dish is in the cart. */
function AddControl({
  item,
  qty,
  onChange,
  large = false,
  compact = false,
}: {
  item: MenuItem;
  qty: number;
  onChange: (delta: number) => void;
  large?: boolean;
  compact?: boolean;
}) {
  const off = item.available_now === false;
  const size = large ? "h-12" : compact ? "h-9" : "h-11";
  const cell = compact ? "w-9" : "w-11";
  if (qty > 0) {
    return (
      <div
        className={cx(
          "flex items-center overflow-hidden rounded-full border border-cream/20 bg-soot-900 font-display font-bold",
          size,
        )}
      >
        <button
          aria-label={`Remove one ${item.name}`}
          className={cx(cell, "h-full text-lg text-cream transition-colors hover:bg-soot-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ghee")}
          onClick={() => onChange(-1)}
        >
          −
        </button>
        <span className="tnum w-7 text-center text-cream" aria-live="polite">
          {qty}
        </span>
        <button
          aria-label={`Add one more ${item.name}`}
          disabled={off}
          className={cx(cell, "h-full bg-ember text-lg text-ember-ink transition-colors hover:bg-ember-hot disabled:cursor-not-allowed disabled:bg-soot-700 disabled:text-cream-faint focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ghee")}
          onClick={() => onChange(1)}
        >
          +
        </button>
      </div>
    );
  }
  return (
    <button
      disabled={off}
      title={off ? "Not available right now" : undefined}
      className={cx(emberBtn, size, large ? "px-7 text-base" : "px-5 text-sm")}
      onClick={() => onChange(1)}
    >
      {large ? "Add to tray" : "Add"}
    </button>
  );
}

/** Spice, allergens, protein and the serving-window line for one dish. */
function DishFacts({ m }: { m: MenuItem }) {
  const off = m.available_now === false;
  return (
    <>
      {(m.spice_level > 0 || m.allergens.length > 0 || m.protein_g != null) && (
        <p className="mt-2 flex flex-wrap items-center gap-1.5 text-xs text-cream-dim">
          {m.spice_level > 0 && (
            <span className="tracking-widest" aria-label={`Spice level ${m.spice_level} of 3`}>
              {SPICE[m.spice_level]}
            </span>
          )}
          {m.allergens.length > 0 && <span className={chip}>⚠ {m.allergens.join(", ")}</span>}
          {m.protein_g != null && (
            <span
              className={cx(
                chip,
                "tnum",
                m.protein_g >= HIGH_PROTEIN_G && "border-ghee/50 text-ghee",
              )}
              title="Protein per serving — AI-estimated, approved by the kitchen"
            >
              💪 {m.protein_g.toFixed(0)} g protein
            </span>
          )}
        </p>
      )}
      {/* Serving-window text is server-built and stays English by design. */}
      {off ? (
        <p className="mt-2">
          <span className="inline-flex items-center gap-1 rounded-full border border-ghee/40 bg-ghee/10 px-2.5 py-0.5 text-xs font-semibold text-ghee">
            ⏰ Not available now{m.serving_windows && <> · Serves {m.serving_windows}</>}
          </span>
        </p>
      ) : (
        <p className="mt-2 text-xs text-cream-dim">
          <span className="font-semibold text-basil">● Serving now</span>
          {m.serving_windows && <> · {m.serving_windows}</>}
        </p>
      )}
    </>
  );
}

/* ---------------------------------- page ---------------------------------- */

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
  // The clock is read after mount: the server renders in its own timezone, so a
  // render-time read would mismatch on hydration (issue #120).
  const [period, setPeriod] = useState<MealPeriod | null>(null);
  const [say, setSay] = useState("");
  const [seed, setSeed] = useState<{ text: string; n: number } | null>(null);
  const [activeCat, setActiveCat] = useState<string | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const scene = useRef<Scene3DHandle>(null);

  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  useEffect(() => {
    setUser(getUser());
    setPeriod(currentMealPeriod());
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

  const inPeriod = (m: MenuItem) =>
    period === null || m.meal_periods.length === 0 || m.meal_periods.includes(period);

  // Show the chip only when at least one dish in the menu is actually scored at
  // or above the threshold — the filter must always return results when offered.
  const hasProteinData = useMemo(
    () => menu.some((m) => (m.protein_g ?? 0) >= HIGH_PROTEIN_G),
    [menu],
  );

  const visible = useMemo(
    () =>
      searchMenu(menu, search).filter(
        (m) =>
          (dietFilter === "all" || (dietFilter === "veg" ? m.is_veg : !m.is_veg)) &&
          (!highProtein || (m.protein_g ?? 0) >= HIGH_PROTEIN_G),
      ),
    [menu, dietFilter, highProtein, search],
  );
  // While searching, the hero and recommendations step aside and the page
  // returns to the top, so the results are the first thing under the search
  // box instead of ~700px below it (where a filter looks like it did nothing).
  const searching = isSearching(search);
  useEffect(() => {
    if (searching) window.scrollTo({ top: 0, behavior: "auto" });
  }, [searching, search]);

  // Current meal period's categories first; within a category, matching items first.
  const categories = useMemo(() => {
    const matches = (cat: string) =>
      period !== null && visible.some((m) => m.category === cat && m.meal_periods.includes(period));
    return [...new Set(visible.map((m) => m.category))].sort(
      (a, b) => Number(matches(b)) - Number(matches(a)),
    );
  }, [visible, period]);
  const catLabel = (cat: string) =>
    visible.find((m) => m.category === cat && m.category_label)?.category_label ?? cat;
  const catId = (cat: string) => `cat-${categories.indexOf(cat)}`;

  // Category tabs follow the scroll position.
  const catKey = categories.join("|");
  useEffect(() => {
    if (categories.length === 0) return;
    const seen = new Map<string, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) seen.set((e.target as HTMLElement).dataset.cat ?? "", e.isIntersecting);
        const first = categories.find((c) => seen.get(c));
        if (first) setActiveCat(first);
      },
      { rootMargin: "-25% 0px -60% 0px" },
    );
    document.querySelectorAll<HTMLElement>("[data-cat]").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [catKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const cartLines = Object.values(cart);
  const cartCount = cartLines.reduce((s, l) => s + l.qty, 0);
  const cartTotal = cartLines.reduce((s, l) => s + parseFloat(l.item.price) * l.qty, 0);
  // Cart lines hold a snapshot of the item from when it was added — resolve
  // against the freshly fetched menu so a dish that slipped out of its serving
  // window since then still blocks checkout (instead of a confusing 409).
  const offWindowLines = cartLines
    .map((l) => menu.find((m) => m.id === l.item.id) ?? l.item)
    .filter((m) => m.available_now === false);
  useEffect(() => {
    if (cartLines.length === 0) setSheetOpen(false);
  }, [cartLines.length]);
  useEffect(() => {
    if (!sheetOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setSheetOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheetOpen]);

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
    if (delta > 0) {
      const key = cutoutKeyFor(englishName(item));
      if (key) scene.current?.hop(key);
    }
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
        },
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

  const openGenie = (text: string) => setSeed((s) => ({ text, n: (s?.n ?? 0) + 1 }));
  const jumpTo = (cat: string) => {
    setActiveCat(cat);
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById(catId(cat))?.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" });
  };

  /** The whole cart: lines you can edit, extras, coupon, total, checkout.
      Rendered in the desktop side panel and in the mobile sheet. */
  const cartPanel = (
    <>
      <div className="flex items-baseline justify-between">
        <h2 className="font-display text-xl font-semibold">Your tray</h2>
        <span className="text-xs text-cream-faint">
          {cartCount} {cartCount === 1 ? "item" : "items"}
        </span>
      </div>
      {cartLines.length === 0 ? (
        <div className="mt-4 rounded-2xl border border-dashed border-cream/15 p-5 text-center">
          <p className="text-sm text-cream-dim">
            Nothing here yet. Add a dish from the menu, or tell Genie what you feel like eating.
          </p>
          <button className={cx(ghostBtn, "mt-3")} onClick={() => openGenie("")}>
            Ask Genie
          </button>
        </div>
      ) : (
        <>
          <ul className="mt-3 divide-y divide-cream/10">
            {cartLines.map((l) => {
              const live = menu.find((m) => m.id === l.item.id) ?? l.item;
              const off = live.available_now === false;
              return (
                <li key={l.item.id} className="flex items-center gap-3 py-3">
                  <div className="min-w-0 flex-1">
                    <p className={cx("truncate font-display text-[15px] font-semibold", off && "text-cream-faint")}>
                      {l.item.name}
                    </p>
                    <p className="tnum text-xs text-cream-faint">
                      {rupee(l.item.price)} each
                      {off && <span className="text-ghee"> · not available now</span>}
                    </p>
                  </div>
                  <AddControl compact item={live} qty={l.qty} onChange={(d) => add(live, d)} />
                  <span className="tnum w-14 text-right font-display font-semibold">
                    {rupee(parseFloat(l.item.price) * l.qty)}
                  </span>
                </li>
              );
            })}
          </ul>
          <CheckoutSuggestions cartIds={Object.keys(cart).map(Number)} menu={menu} onAdd={(item) => add(item, 1)} />
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <input
              aria-label="Coupon code"
              className="h-10 min-w-0 flex-1 rounded-full border border-cream/15 bg-soot-950 px-3.5 text-xs uppercase text-cream outline-none placeholder:text-cream-faint focus:border-ghee"
              placeholder="Coupon code"
              value={couponCode}
              onChange={(e) => {
                setCouponCode(e.target.value.toUpperCase());
                setCoupon(null);
                setCouponError(null);
              }}
            />
            <button className={cx(ghostBtn, "h-10 disabled:opacity-50")} disabled={couponCode.length < 2 || !!coupon} onClick={applyCoupon}>
              {coupon ? "✓ Applied" : "Apply"}
            </button>
          </div>
          {couponError && <p className="mt-2 text-xs font-semibold text-[#FF8B8B]">{couponError}</p>}
          <dl className="mt-4 space-y-1.5 border-t border-cream/10 pt-3 text-sm">
            <div className="flex justify-between">
              <dt className="text-cream-dim">Subtotal</dt>
              <dd className="tnum">{rupee(cartTotal)}</dd>
            </div>
            {coupon && (
              <div className="flex justify-between text-basil">
                <dt>Coupon {coupon.code}</dt>
                <dd className="tnum">−{rupee(coupon.discount)}</dd>
              </div>
            )}
            <div className="flex justify-between text-xs text-cream-faint">
              <dt>GST</dt>
              <dd>added at checkout</dd>
            </div>
          </dl>
          {offWindowLines.length > 0 && (
            <div className="mt-3 space-y-1 text-xs">
              {offWindowLines.map((m) => (
                <p key={m.id} className="rounded-xl border border-ghee/40 bg-ghee/10 px-3 py-1.5 font-semibold text-ghee">
                  ⏰ {m.name} is not available right now
                  {m.serving_windows && <> (serves {m.serving_windows})</>} — remove it to checkout.
                </p>
              ))}
            </div>
          )}
          <button
            className={cx(emberBtn, "mt-4 h-12 w-full text-base shadow-ember")}
            disabled={placing || offWindowLines.length > 0}
            title={offWindowLines.length > 0 ? "Remove the unavailable dishes to checkout" : undefined}
            onClick={checkout}
          >
            {placing ? "Placing…" : user ? "Checkout" : "Log in to checkout"}
          </button>
        </>
      )}
    </>
  );

  return (
    <main className="min-h-screen bg-soot-950 pb-28 text-cream lg:pb-10">
      {/* ── app bar: search is always one tap away ── */}
      <header className="sticky top-0 z-40 border-b border-cream/10 bg-soot-950/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1280px] flex-wrap items-center gap-x-1.5 px-3 sm:gap-x-3 lg:px-6">
          <p className="flex h-14 items-center font-display text-lg font-bold tracking-wide max-[359px]:text-base sm:text-xl">
            Dosa<span className="text-ember">Dash</span>
          </p>
          <input
            type="search"
            aria-label="Search dishes"
            className="order-last mb-2.5 h-10 w-full basis-full rounded-full border border-cream/15 bg-soot-900 px-4 text-cream placeholder:text-cream-faint text-[15px] outline-none focus:border-ghee lg:order-none lg:mb-0 lg:ml-4 lg:w-auto lg:max-w-md lg:flex-1 lg:basis-auto"
            placeholder="Search dosa, idli, biryani…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setSearch("")}
          />
          <span className="flex-1" />
          <button
            className="relative flex overflow-hidden rounded-full border border-cream/15 text-[13px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ghee"
            title={lang === "ta" ? "Switch to English" : "தமிழில் காட்டு"}
            onClick={() => switchLang(lang === "ta" ? "en" : "ta")}
          >
            <span className={cx("px-2 py-1.5 max-[359px]:px-1.5 sm:px-3", lang === "en" ? "bg-cream font-semibold text-soot-950" : "text-cream-dim")}>
              EN
            </span>
            <span className={cx("px-2 py-1.5 max-[359px]:px-1.5 sm:px-3", lang === "ta" ? "bg-cream font-semibold text-soot-950" : "text-cream-dim")}>
              தமிழ்
            </span>
            {/* name = the visible text plus this (WCAG 2.5.3: label in name) */}
            <span className="sr-only">, menu language: switch to {lang === "ta" ? "English" : "Tamil"}</span>
          </button>
          <FeedbackButton variant="icon" />
          <Link href="/orders" className={cx(ghostBtn, "max-sm:px-2.5")} aria-label="Your orders">
            <span aria-hidden="true" className="sm:hidden">📦</span>
            <span className="max-sm:hidden">Orders</span>
          </Link>
          <Link href="/demo" className={cx(ghostBtn, "max-md:hidden")} title="Demo guide: credentials + test cards">
            Demo
          </Link>
          {user ? (
            <button
              className={cx(ghostBtn, "max-sm:px-2.5")}
              onClick={() => {
                clearSession();
                setUser(null);
              }}
            >
              Logout
            </button>
          ) : (
            <button className={cx(ghostBtn, "max-sm:px-2.5")} onClick={() => setShowLogin(true)}>
              Login
            </button>
          )}
        </div>
      </header>

      <div className="mx-auto max-w-[1280px] px-3 lg:px-6">
        {/* ── hero: a compact banner. The scene is here and nowhere else. ── */}
        <section className={cx("relative mt-3", searching && "hidden")} aria-labelledby="hero-title">
          <div className="relative h-[180px] overflow-hidden rounded-3xl border border-cream/10 lg:h-[290px]">
            <Scene3D ref={scene} />
            <div className="absolute inset-y-0 left-0 hidden w-[64%] bg-gradient-to-r from-soot-950 via-soot-950/85 to-transparent lg:block" />
          </div>
          <div className="mt-4 lg:absolute lg:inset-y-0 lg:left-0 lg:mt-0 lg:flex lg:w-[50%] lg:flex-col lg:justify-center lg:pl-9">
            <p className={eyebrow}>{period ? GREETING[period] : "Chennai"}</p>
            <h1 id="hero-title" className="mt-2 font-display text-[30px] font-semibold leading-[1.05] tracking-[-0.025em] lg:text-[44px]">
              Say it. We’ll <span className="text-ember">make</span> it.
            </h1>
            <form
              className="mt-4 flex items-center gap-2 rounded-full border border-cream/15 bg-soot-900 p-1.5 pl-4 focus-within:border-ember"
              onSubmit={(e) => {
                e.preventDefault();
                openGenie(say);
                setSay("");
              }}
            >
              <input
                aria-label="Tell Dosa Genie what you want to eat"
                className="h-10 min-w-0 flex-1 border-0 bg-transparent text-base text-cream outline-none placeholder:text-cream-faint"
                placeholder="2 masala dosa, 1 filter coffee…"
                autoComplete="off"
                value={say}
                onChange={(e) => setSay(e.target.value)}
              />
              <button type="submit" className={cx(emberBtn, "h-10 shrink-0 px-4 text-sm shadow-ember")}>
                Ask Genie
              </button>
            </form>
            <p className="mt-2 text-[13px] text-cream-faint">
              Opens a chat that drafts your order in English, Tanglish, Hinglish or <span lang="ta">தமிழ்</span>, and
              checks every dish against today’s menu.
            </p>
          </div>
        </section>

        <div className="mt-5 lg:grid lg:grid-cols-[minmax(0,1fr)_360px] lg:items-start lg:gap-6">
          {/* ── menu ── */}
          <div className="min-w-0">
            <div className="sticky top-[106px] z-30 -mx-3 border-b border-cream/10 bg-soot-950/95 px-3 py-2.5 backdrop-blur lg:top-14 lg:mx-0 lg:rounded-b-2xl lg:px-0">
              <nav aria-label="Menu categories" className="scrollbar-none flex gap-2 overflow-x-auto pb-1">
                {categories.map((cat) => (
                  <button
                    key={cat}
                    aria-current={activeCat === cat}
                    className={cx(
                      "h-10 shrink-0 rounded-full border px-4 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ghee",
                      activeCat === cat
                        ? "border-cream bg-cream text-soot-950"
                        : "border-cream/15 text-cream-dim hover:border-cream/40 hover:text-cream",
                    )}
                    onClick={() => jumpTo(cat)}
                  >
                    {catLabel(cat)}
                  </button>
                ))}
              </nav>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <div role="group" aria-label="Diet filter" className="flex overflow-hidden rounded-full border border-cream/15 text-[13px]">
                  {(["all", "veg", "nonveg"] as const).map((f) => (
                    <button
                      key={f}
                      type="button"
                      aria-pressed={dietFilter === f}
                      className={cx(
                        "h-9 px-3.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ghee",
                        dietFilter === f ? "bg-basil font-semibold text-soot-950" : "text-cream-dim hover:bg-soot-700",
                      )}
                      onClick={() => setDietFilter(f)}
                    >
                      {f === "all" ? "All" : f === "veg" ? "Veg" : "Non-veg"}
                    </button>
                  ))}
                </div>
                {hasProteinData && (
                  <button
                    type="button"
                    aria-pressed={highProtein}
                    title={`Show only dishes with ${HIGH_PROTEIN_G} g protein or more per serving`}
                    className={cx(
                      "h-9 rounded-full border px-3.5 text-[13px] font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ghee",
                      highProtein ? "border-ghee bg-ghee/15 text-ghee" : "border-cream/15 text-cream-dim hover:border-ghee",
                    )}
                    onClick={() => setHighProtein((on) => !on)}
                  >
                    💪 High protein
                  </button>
                )}
                {menu.length > 0 && (
                  <span className="ml-auto text-xs text-cream-faint">
                    {visible.length} of {menu.length} dishes
                  </span>
                )}
              </div>
            </div>

            {error && <p className="mt-4 text-sm font-semibold text-[#FF5D5D]">{error}</p>}
            {menu.length === 0 && !error && <p className="mt-6 text-sm text-cream-dim">Loading the menu…</p>}

            {searching ? (
              <p className="mt-5 flex flex-wrap items-center gap-3 text-sm text-cream-dim" role="status">
                <span>
                  <strong className="text-cream">
                    {visible.length} {visible.length === 1 ? "dish" : "dishes"}
                  </strong>{" "}
                  for “{search.trim()}”
                </span>
                <button className={ghostBtn} onClick={() => setSearch("")}>
                  Clear search
                </button>
              </p>
            ) : (
              <Recommendations cartIds={Object.keys(cart).map(Number)} menu={menu} onAdd={(item) => add(item, 1)} />
            )}

            {categories.map((cat) => {
              const inCat = visible.filter((m) => m.category === cat);
              return (
                <section key={cat} id={catId(cat)} data-cat={cat} className="scroll-mt-[215px] pt-8 lg:scroll-mt-[170px]">
                  <div className="mb-3 flex items-baseline gap-3">
                    <h2 className="font-display text-[22px] font-semibold">{catLabel(cat)}</h2>
                    <span className="text-xs text-cream-faint">{inCat.length} dishes</span>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    {inCat
                      .sort((a, b) =>
                        highProtein
                          ? (b.protein_g ?? 0) - (a.protein_g ?? 0)
                          : Number(inPeriod(b)) - Number(inPeriod(a)),
                      )
                      .map((m) => {
                        const off = m.available_now === false;
                        const cutout = cutoutKeyFor(englishName(m));
                        // A dish with no kitchen photo borrows its scene cutout (also AI-made, so also labelled).
                        const thumb = m.image_url ?? (cutout ? `/scene/${cutout}.webp` : null);
                        return (
                          <article
                            key={m.id}
                            className={cx(
                              "flex gap-3.5 rounded-2xl border p-3 transition-colors",
                              off ? "border-cream/10 bg-soot-900/60" : "border-cream/10 bg-soot-900 hover:border-cream/30",
                            )}
                            onMouseEnter={cutout ? () => scene.current?.select(cutout) : undefined}
                          >
                            {!thumb && (
                              // No kitchen photo yet: a quiet tile keeps every card the same shape.
                              <div
                                aria-hidden="true"
                                className={cx(
                                  "grid h-[104px] w-[104px] shrink-0 place-items-center rounded-xl border border-cream/10 bg-soot-800 font-display text-4xl font-semibold text-cream-faint",
                                  off && "opacity-40",
                                )}
                              >
                                {englishName(m).charAt(0)}
                              </div>
                            )}
                            {thumb && (
                              <div className="relative h-[104px] w-[104px] shrink-0">
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img
                                  src={thumb}
                                  alt=""
                                  width={104}
                                  height={104}
                                  loading="lazy"
                                  className={cx(
                                    "h-[104px] w-[104px] rounded-xl bg-soot-950",
                                    m.image_url ? "object-cover" : "object-contain p-1",
                                    off && "opacity-40 saturate-50",
                                  )}
                                />
                                {(m.image_ai || !m.image_url) && (
                                  <span
                                    className="absolute bottom-1 left-1 rounded-full bg-soot-950/85 px-1.5 py-0.5 text-[11px] font-semibold text-ghee"
                                    title="This picture was generated by AI"
                                  >
                                    ✨ AI
                                  </span>
                                )}
                              </div>
                            )}
                            <div className="flex min-w-0 flex-1 flex-col">
                              <h3 className="flex items-start gap-2">
                                <VegMark veg={m.is_veg} />
                                <span className={cx("break-words font-display text-base font-semibold leading-snug", off && "text-cream-faint")}>
                                  {m.name}
                                </span>
                              </h3>
                              <p className={cx("mt-1 line-clamp-2 text-sm text-cream-dim", off && "opacity-70")}>{m.description}</p>
                              <DishFacts m={m} />
                              <div className="mt-auto flex items-center pt-3">
                                <span className={cx("tnum font-display text-lg font-semibold", off && "text-cream-faint")}>
                                  {rupee(m.price)}
                                </span>
                                <span className="flex-1" />
                                <AddControl item={m} qty={cart[m.id]?.qty ?? 0} onChange={(d) => add(m, d)} />
                              </div>
                            </div>
                          </article>
                        );
                      })}
                  </div>
                </section>
              );
            })}
            {menu.length > 0 && visible.length === 0 && (
              <p className="mt-8 rounded-2xl border border-cream/15 bg-soot-900 p-4 text-sm text-cream-dim">
                {highProtein ? (
                  <>
                    No dish on today&apos;s menu is scored at {HIGH_PROTEIN_G} g protein or more
                    {dietFilter !== "all" && ` among the ${dietFilter === "veg" ? "veg" : "non-veg"} dishes`}. Switch
                    the 💪 filter off to see everything.
                  </>
                ) : (
                  <>
                    Nothing on the menu matches{searching && <> “{search.trim()}”</>}. Try a shorter word, clear the
                    search, or set the diet filter back to <strong>All</strong>.
                  </>
                )}
              </p>
            )}

            {/* ── how the AI keeps the order right (was a full-screen chapter) ── */}
            <section className="mt-14 border-t border-cream/10 pt-8" aria-labelledby="trust-title">
              <h2 id="trust-title" className={eyebrow}>
                A kitchen that checks its own work
              </h2>
              <ul className="mt-4 grid gap-3 sm:grid-cols-2">
                {[
                  ["It never invents a dish", "Every item the assistant drafts is looked up in the live menu before your order is placed."],
                  ["It knows what is on the tawa now", "Ask for something outside its serving hours and it tells you when it is served."],
                  ["An honest arrival time", "The delivery estimate is predicted from kitchen and delivery timings, not a fixed guess."],
                  ["A photo check before it leaves", "The kitchen can photograph your plate and a vision model checks the right dishes are on it."],
                ].map(([title, body]) => (
                  <li key={title} className="rounded-2xl border border-cream/10 bg-soot-900 p-4">
                    <h3 className="font-display text-[15px] font-semibold">{title}</h3>
                    <p className="mt-1 text-sm text-cream-dim">{body}</p>
                  </li>
                ))}
              </ul>
              <FeedbackButton variant="card" />
              <p className="mt-6 text-xs text-cream-faint">
                The kitchen scene at the top of this page and pictures marked ✨ AI are AI-generated. Phone numbers are
                removed before any message reaches the assistant.
              </p>
            </section>
          </div>

          {/* ── cart: always in view on desktop ── */}
          <aside
            data-tray-anchor
            aria-label="Your tray"
            className="sticky top-[72px] hidden max-h-[calc(100vh-88px)] overflow-y-auto rounded-3xl border border-cream/10 bg-soot-900 p-5 lg:block"
          >
            {cartPanel}
          </aside>
        </div>
      </div>

      {/* ── cart on phones: a bar that opens the same panel as a sheet ── */}
      {cartLines.length > 0 && !sheetOpen && (
        <button
          data-tray-anchor
          className={cx(emberBtn, "fixed inset-x-3 bottom-3 z-40 h-14 justify-between px-5 lg:hidden")}
          onClick={() => setSheetOpen(true)}
        >
          <span>
            View tray · {cartCount} {cartCount === 1 ? "item" : "items"}
          </span>
          <span className="tnum">{rupee(cartTotal)}</span>
        </button>
      )}
      {sheetOpen && (
        <div className="fixed inset-0 z-50 flex items-end bg-black/65 lg:hidden" onClick={() => setSheetOpen(false)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Your tray"
            className="max-h-[88vh] w-full overflow-y-auto rounded-t-3xl border-t border-cream/15 bg-soot-900 p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex justify-end">
              <button className={ghostBtn} onClick={() => setSheetOpen(false)}>
                Close
              </button>
            </div>
            {cartPanel}
          </div>
        </div>
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
        seed={seed}
        launcher={false}
        onRequireLogin={() => setShowLogin(true)}
        onPlaceOrder={async (items) => {
          const order = await api<Order>("/orders", { method: "POST", auth: true, body: { items } });
          setTracking(order);
        }}
      />
    </main>
  );
}
