"use client";

import { useEffect, useRef, useState } from "react";
import { api, type MenuItem } from "../../lib/api";
import { Chip } from "./ui";

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
    <div className="mx-auto mb-2 flex max-w-4xl flex-wrap gap-2">
      {visible.map((s) => (
        <Chip
          key={s.item_id}
          surface="dark"
          className="flex items-center gap-1.5"
          onClick={() => onAdd(byId.get(s.item_id)!)}
        >
          <span>{s.kind === "combo" ? "🧩" : "✨"}</span>
          <span className="font-semibold text-brass-300">
            + {s.name} <span className="tnum">₹{parseFloat(s.price).toFixed(0)}</span>
          </span>
          <span className="text-leaf-200">· {s.reason}</span>
        </Chip>
      ))}
    </div>
  );
}
