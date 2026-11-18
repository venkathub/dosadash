"use client";

import { useEffect, useRef, useState } from "react";
import { api, type MenuItem } from "../../lib/api";
import { cutoutKeyFor } from "../../lib/scene";
import { FssaiMark } from "./ui";

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
    <section className="mt-6" aria-labelledby="recs-title">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="recs-title" className="font-display text-lg font-semibold text-cream">
          You might like
        </h2>
        <span className="text-[13px] text-cream-dim">{SOURCE_LABEL[recs.source] ?? "Suggestions"}</span>
        <span className="ai-meta ml-auto">🤖 recsys · {recs.model_version ?? recs.source}</span>
      </div>
      <div
        tabIndex={0}
        role="group"
        aria-label="Recommended dishes, scroll sideways for more"
        className="scrollbar-none -mx-1 flex gap-2.5 overflow-x-auto px-1 pb-1 [mask-image:linear-gradient(to_right,black_calc(100%-48px),transparent)]"
      >
        {items.map((r) => {
          const item = byId.get(r.item_id)!;
          const cutout = cutoutKeyFor(item.canonical_name ?? item.name);
          const thumb = item.image_url ?? (cutout ? `/scene/${cutout}.webp` : null);
          return (
            <button
              key={r.item_id}
              className="group flex w-[224px] shrink-0 items-center gap-3 rounded-2xl border border-cream/10 bg-soot-900 p-2 pr-3 text-left transition-colors duration-150 hover:border-cream/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ghee"
              onClick={() => onAdd(item)}
            >
              {thumb ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={thumb}
                  alt=""
                  width={56}
                  height={56}
                  loading="lazy"
                  className={`h-14 w-14 shrink-0 rounded-xl bg-soot-950 ${item.image_url ? "object-cover" : "object-contain p-0.5"}`}
                />
              ) : (
                <span className="grid h-14 w-14 shrink-0 place-items-center rounded-xl bg-soot-800">
                  <FssaiMark veg={r.is_veg} />
                </span>
              )}
              <span className="min-w-0 flex-1">
                <span className="sr-only">Add </span>
                <span className="line-clamp-2 font-display text-sm font-semibold leading-tight text-cream">{r.name}</span>
                <span className="tnum text-sm text-cream-dim">₹{parseFloat(r.price).toFixed(0)}</span>
              </span>
              <span
                aria-hidden="true"
                className="grid h-8 w-8 shrink-0 place-items-center rounded-full border border-cream/20 font-display text-lg text-cream transition-colors group-hover:border-transparent group-hover:bg-ember group-hover:text-ember-ink"
              >
                +
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
