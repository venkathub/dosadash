"use client";

/** Rate a DELIVERED order (Phase 8) — one review per order. Shows the
 *  owner's published reply once there is one. */

import { useEffect, useState } from "react";
import { ApiError, api } from "../../lib/api";

type Review = {
  id: number;
  order_id: number;
  rating: number;
  text: string;
  owner_reply: string | null;
};

export function ReviewBox({ orderId }: { orderId: number }) {
  const [review, setReview] = useState<Review | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [open, setOpen] = useState(false);
  const [rating, setRating] = useState(0);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Review>(`/orders/${orderId}/review`, { auth: true })
      .then(setReview)
      .catch(() => setReview(null))
      .finally(() => setLoaded(true));
  }, [orderId]);

  const submit = async () => {
    if (!rating) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api<Review>(`/orders/${orderId}/review`, {
        method: "POST",
        auth: true,
        body: { rating, text: text.trim() },
      });
      setReview(created);
      setOpen(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save review");
    } finally {
      setBusy(false);
    }
  };

  if (!loaded) return null;

  if (review) {
    return (
      <div className="mt-2 rounded bg-amber-50 p-2 text-xs">
        <span className="text-amber-500">{"★".repeat(review.rating)}{"☆".repeat(5 - review.rating)}</span>
        {review.text && <span className="ml-2 text-stone-600">“{review.text}”</span>}
        {review.owner_reply && (
          <p className="mt-1 text-stone-600">
            <b>DosaDash:</b> {review.owner_reply}
          </p>
        )}
      </div>
    );
  }

  if (!open) {
    return (
      <button className="mt-2 text-xs text-amber-600 underline" onClick={() => setOpen(true)}>
        ★ Rate this order
      </button>
    );
  }

  return (
    <div className="mt-2 rounded bg-amber-50 p-2">
      <div className="flex items-center gap-1 text-lg">
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            aria-label={`${n} star`}
            className={n <= rating ? "text-amber-500" : "text-stone-300"}
            onClick={() => setRating(n)}
          >
            ★
          </button>
        ))}
      </div>
      <textarea
        className="mt-1 w-full rounded border border-amber-200 p-2 text-xs"
        rows={2}
        placeholder="How was it? (optional)"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      {error && <p className="text-xs text-red-600">{error}</p>}
      <div className="mt-1 flex gap-2">
        <button
          className="rounded bg-amber-500 px-3 py-1 text-xs font-bold disabled:opacity-40"
          disabled={!rating || busy}
          onClick={submit}
        >
          Submit
        </button>
        <button className="text-xs text-stone-500 underline" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}
