"use client";

import { useEffect, useRef, useState } from "react";
import { api, type MenuItem } from "../../lib/api";

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
    <section className="mt-4 rounded-lg border border-amber-300 bg-amber-100/60 p-3">
      <h2 className="text-sm font-bold text-amber-900">
        ✨ You might like{" "}
        <span className="font-normal text-amber-700">
          · {SOURCE_LABEL[recs.source] ?? "Suggestions"}
        </span>
      </h2>
      <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
        {items.map((r) => (
          <button
            key={r.item_id}
            className="flex shrink-0 items-center gap-2 rounded-full border border-amber-300 bg-white px-3 py-1.5 text-sm hover:bg-amber-50"
            onClick={() => onAdd(byId.get(r.item_id)!)}
            title="Add to cart"
          >
            <span className={r.is_veg ? "text-green-600" : "text-red-600"}>
              {r.is_veg ? "🟢" : "🔴"}
            </span>
            <span className="font-semibold">{r.name}</span>
            <span className="text-stone-500">₹{parseFloat(r.price).toFixed(0)}</span>
            <span className="font-bold text-amber-600">+</span>
          </button>
        ))}
      </div>
    </section>
  );
}
