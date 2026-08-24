"use client";

import { useEffect, useRef, useState } from "react";
import { api, type MenuItem } from "../../lib/api";
import { FssaiMark, PosterBlock, Zari } from "./ui";

type Rec = { item_id: number; name: string; price: string; is_veg: boolean; score: number };
type RecsResponse = { items: Rec[]; source: string; model_version: string | null };

const SOURCE_LABEL: Record<string, string> = {
  als: "Because you've ordered with us before",
  embedding: "Goes well with your cart",
  popular: "Popular right now",
};

/** "You might like" strip: personalized (ALS) for returning customers,
 * cart-similarity for cold-start, bestsellers otherwise. Renders nothing
 * when the recommender is unavailable — never blocks the menu. */
export default function Recommendations({
  cartIds,
  menu,
  onAdd,
}: {
  cartIds: number[];
  menu: MenuItem[];
  onAdd: (item: MenuItem) => void;
}) {
  const [recs, setRecs] = useState<RecsResponse | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (menu.length === 0) return;
    if (timer.current) clearTimeout(timer.current);
    // Debounce: cart edits arrive in bursts while people make up their minds.
    timer.current = setTimeout(() => {
      const cart = cartIds.join(",");
      api<RecsResponse>(`/recs?cart=${cart}&k=6`, { auth: true })
        .then(setRecs)
        .catch(() => setRecs(null));
    }, 400);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [cartIds.join(","), menu.length]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!recs || recs.items.length === 0) return null;
  const byId = new Map(menu.map((m) => [m.id, m]));
  const items = recs.items.filter((r) => byId.has(r.item_id));
  if (items.length === 0) return null;

  return (
    <section className="mt-6">
      <div className="mb-1 flex flex-wrap items-center gap-2.5">
        <PosterBlock tone="turmeric">✨ You might like</PosterBlock>
        <span className="ai-meta ai-meta-light">
          🤖 recsys · {recs.model_version ?? recs.source}
        </span>
        <span className="text-[11.5px] text-muted">
          {SOURCE_LABEL[recs.source] ?? "Suggestions"}
        </span>
      </div>
      <Zari className="mb-3" />
      <div className="flex gap-2 overflow-x-auto pb-1.5">
        {items.map((r) => (
          <button
            key={r.item_id}
            className="flex shrink-0 items-center gap-2 rounded-xl border-2 border-indigo-900 bg-paper px-3 py-1.5 text-sm shadow-pop-sm transition-colors duration-150 hover:bg-turmeric-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500"
            onClick={() => onAdd(byId.get(r.item_id)!)}
            title="Add to cart"
          >
            <FssaiMark veg={r.is_veg} />
            <span className="font-semibold text-ink">{r.name}</span>
            <span className="tnum font-display font-bold text-ink">
              ₹{parseFloat(r.price).toFixed(0)}
            </span>
            <span className="font-display font-bold text-magenta-600">+</span>
          </button>
        ))}
      </div>
    </section>
  );
}
