"use client";

import { useEffect, useRef, useState } from "react";
import { api, type MenuItem } from "../../lib/api";
import { Chip, Eyebrow, cx } from "./ui";

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
    <section className="mt-4">
      <Eyebrow>
        <span className="text-brass-600">✨ You might like</span>{" "}
        <span className="normal-case tracking-normal text-ink-400">
          · {SOURCE_LABEL[recs.source] ?? "Suggestions"}
        </span>
      </Eyebrow>
      <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
        {items.map((r) => (
          <Chip
            key={r.item_id}
            surface="light"
            className="flex shrink-0 items-center gap-2 py-1.5 text-sm shadow-card"
            onClick={() => onAdd(byId.get(r.item_id)!)}
            title="Add to cart"
          >
            <span
              className={cx(
                "inline-flex h-3 w-3 items-center justify-center rounded-[3px] border",
                r.is_veg ? "border-veg-500" : "border-chili-500",
              )}
            >
              <span
                className={cx(
                  "h-1 w-1 rounded-full",
                  r.is_veg ? "bg-veg-500" : "bg-chili-500",
                )}
              />
            </span>
            <span className="font-semibold">{r.name}</span>
            <span className="tnum text-ink-600">₹{parseFloat(r.price).toFixed(0)}</span>
            <span className="font-bold text-brass-600">+</span>
          </Chip>
        ))}
      </div>
    </section>
  );
}
