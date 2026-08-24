"use client";

/** Rate a DELIVERED order (Phase 8) — one review per order. Shows the
 *  owner's published reply once there is one. */

import { useEffect, useState } from "react";
import { ApiError, api } from "../../lib/api";
import { Btn, Textarea, cx } from "../components/ui";

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
      <div className="mt-2 rounded-lg border-[1.5px] border-sand-300 bg-offwhite p-2 text-xs">
        <span className="text-turmeric-600">{"★".repeat(review.rating)}{"☆".repeat(5 - review.rating)}</span>
        {review.text && <span className="ml-2 text-muted">“{review.text}”</span>}
        {review.owner_reply && (
          <p className="mt-1 text-muted">
            <b className="text-ink">DosaDash:</b> {review.owner_reply}
          </p>
        )}
      </div>
    );
  }

  if (!open) {
    return (
      <button
        className="mt-2 font-display text-xs font-bold text-magenta-600 underline underline-offset-4 transition-colors duration-150 hover:text-magenta-500"
        onClick={() => setOpen(true)}
      >
        ★ Rate this order
      </button>
    );
  }

  return (
    <div className="mt-2 rounded-lg border-[1.5px] border-sand-300 bg-offwhite p-2">
      <div className="flex items-center gap-1 text-2xl">
        {[1, 2, 3, 4, 5].map((n) => (
          <button
            key={n}
            aria-label={`${n} star`}
            className={cx(
              "transition-colors duration-150",
              n <= rating ? "text-turmeric-500" : "text-sand-300 hover:text-turmeric-400",
            )}
            onClick={() => setRating(n)}
          >
            ★
          </button>
        ))}
      </div>
      <Textarea
        tone="light"
        className="mt-1 w-full text-xs"
        rows={2}
        placeholder="How was it? (optional)"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      {error && <p className="text-xs font-semibold text-chili">{error}</p>}
      <div className="mt-1 flex items-center gap-2">
        <Btn variant="magenta" size="sm" disabled={!rating || busy} onClick={submit}>
          Submit
        </Btn>
        <button
          className="text-xs text-faint underline underline-offset-4 transition-colors duration-150 hover:text-muted"
          onClick={() => setOpen(false)}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
