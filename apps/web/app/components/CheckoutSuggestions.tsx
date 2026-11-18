"use client";

import { useEffect, useRef, useState } from "react";
import { api, type MenuItem } from "../../lib/api";

type Suggestion = {
  item_id: number;
  name: string;
  price: string;
  is_veg: boolean;
  kind: "combo" | "pairing";
  reason: string;
};
type SuggestResponse = { suggestions: Suggestion[]; source: string };

/** Checkout add-on chips: combo completion + personalized pairing gaps.
 * Deterministic rules ranked by the recommender (synthetic A/B: attach
 * 15.6% vs 12.8% random, AOV +4.9% vs control). Renders nothing when the
 * suggester is unavailable — checkout never blocks. */
export default function CheckoutSuggestions({
  cartIds,
  menu,
  onAdd,
}: {
  cartIds: number[];
  menu: MenuItem[];
  onAdd: (item: MenuItem) => void;
}) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (cartIds.length === 0) {
      setSuggestions([]);
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      api<SuggestResponse>(`/recs/checkout?cart=${cartIds.join(",")}`, { auth: true })
        .then((r) => setSuggestions(r.suggestions))
        .catch(() => setSuggestions([]));
    }, 400);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [cartIds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  const byId = new Map(menu.map((m) => [m.id, m]));
  const visible = suggestions.filter((s) => byId.has(s.item_id));
  if (visible.length === 0) return null;

  return (
    <div className="mb-3 grid gap-1.5">
      {visible.map((s) => (
        <button
          key={s.item_id}
          className="group flex w-full items-center gap-2.5 rounded-2xl border border-cream/10 bg-soot-800 px-3 py-2 text-left text-[13px] transition-colors duration-150 hover:border-cream/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ghee"
          onClick={() => onAdd(byId.get(s.item_id)!)}
        >
          <span aria-hidden="true">{s.kind === "combo" ? "🧩" : "✨"}</span>
          <span className="min-w-0 flex-1">
            <span className="font-semibold text-cream">
              {s.name} <span className="tnum font-display">₹{parseFloat(s.price).toFixed(0)}</span>
            </span>
            <span className="block text-xs text-cream-dim">{s.reason}</span>
          </span>
          <span aria-hidden="true" className="font-display text-base text-cream-dim group-hover:text-ember">
            +
          </span>
        </button>
      ))}
    </div>
  );
}
