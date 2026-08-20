"use client";

/** Reviews inbox (Phase 8): aspect-sentiment auto-tags, trend alerts and
 *  AI-drafted owner replies. The LLM tags and drafts; a human publishes —
 *  a reply ships as AI_DRAFT only when published verbatim. */

import { useCallback, useState } from "react";
import {
  Badge,
  Btn,
  EmptyState,
  Eyebrow,
  SectionHeading,
  Select,
  Textarea,
  statusBadgeTone,
} from "../components/ui";
import { AdminApiError, adminApi } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

type AspectTag = { aspect: string; sentiment: "POSITIVE" | "NEGATIVE" };

type AdminReview = {
  id: number;
  order_id: number;
  rating: number;
  text: string;
  sentiment: string | null;
  aspects: AspectTag[] | null;
  scored_model: string | null;
  reply_draft: string | null;
  reply_draft_model: string | null;
  owner_reply: string | null;
  reply_source: string | null;
  created_at: string | null;
  dishes: string[];
};

type ReviewList = { reviews: AdminReview[]; total: number; unscored: number };

type TrendPoint = { week_start: string; count: number };
type AspectTrend = { aspect: string; points: TrendPoint[]; alert: boolean; top_dishes: string[] };
type Trends = { weeks: number; aspects: AspectTrend[] };

function Stars({ n }: { n: number }) {
  return <span className="text-brass-400">{"★".repeat(n)}{"☆".repeat(5 - n)}</span>;
}

function AspectChips({ aspects }: { aspects: AspectTag[] | null }) {
  if (!aspects?.length) return null;
  return (
    <span className="flex flex-wrap gap-1">
      {aspects.map((a) => (
        <Badge key={a.aspect} tone={a.sentiment === "NEGATIVE" ? "danger" : "success"} className="text-[10px]">
          {a.sentiment === "NEGATIVE" ? "▼" : "▲"} {a.aspect}
        </Badge>
      ))}
    </span>
  );
}

function ReviewCard({
  review,
  onAction,
  onError,
}: {
  review: AdminReview;
  onAction: () => void;
  onError: (msg: string) => void;
}) {
  const [reply, setReply] = useState(review.reply_draft ?? "");
  const [busy, setBusy] = useState(false);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onAction();
    } catch (e) {
      onError(e instanceof AdminApiError ? e.message : "action failed");
    } finally {
      setBusy(false);
    }
  };

  const draftReply = () =>
    act(async () => {
      const updated = await adminApi<AdminReview>(`/admin/reviews/${review.id}/draft-reply`, { method: "POST" });
      setReply(updated.reply_draft ?? "");
    });

  const publish = () =>
    act(() => adminApi(`/admin/reviews/${review.id}/reply`, { method: "POST", body: { reply } }));

  return (
    <div className="rounded-lg bg-leaf-800 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Stars n={review.rating} />
        {review.sentiment ? (
          <Badge tone={statusBadgeTone(review.sentiment)}>{review.sentiment}</Badge>
        ) : (
          <Badge tone="neutral">unscored</Badge>
        )}
        <AspectChips aspects={review.aspects} />
        <span className="ml-auto text-xs text-leaf-200/70">
          order #{review.order_id} · {review.dishes.join(", ")}
          {review.created_at && ` · ${new Date(review.created_at).toLocaleDateString("en-IN")}`}
        </span>
      </div>
      {review.text && <p className="mt-2 text-leaf-200">“{review.text}”</p>}
      {review.owner_reply ? (
        <p className="mt-2 rounded-lg bg-leaf-700/60 p-2 text-xs text-leaf-200">
          <span className="text-veg-200">✔ replied ({review.reply_source}):</span> {review.owner_reply}
        </p>
      ) : (
        review.text && (
          <div className="mt-2 flex flex-wrap items-start gap-2">
            <Textarea
              tone="dark"
              className="min-h-[3rem] w-full max-w-xl flex-1 px-2 py-1"
              placeholder="Owner reply…"
              value={reply}
              onChange={(e) => setReply(e.target.value)}
            />
            <span className="flex flex-col gap-1">
              <Btn variant="ghost" size="sm" disabled={busy} onClick={draftReply}>
                🤖 draft reply
              </Btn>
              <Btn variant="gold" size="sm" disabled={busy || !reply.trim()} onClick={publish}>
                publish
              </Btn>
            </span>
            {review.reply_draft_model && (
              <span className="w-full"><span className="ai-meta">🤖 draft by {review.reply_draft_model}</span></span>
            )}
          </div>
        )
      )}
    </div>
  );
}

function TrendsPanel() {
  const load = useCallback(() => adminApi<Trends>("/admin/reviews/trends?weeks=8"), []);
  const { data, error } = useLoad(load);
  if (error) return <ErrorBar msg={error} />;
  if (!data) return null;
  const alerts = data.aspects.filter((a) => a.alert);
  const max = Math.max(1, ...data.aspects.flatMap((a) => a.points.map((p) => p.count)));
  return (
    <div className="mb-4 rounded-lg bg-leaf-800 p-3">
      <div className="mb-2">
        <Eyebrow>Reviews</Eyebrow>
        <SectionHeading as="h3" kolam={false} className="text-base text-leaf-100">
          Complaint trends <span className="font-sans text-xs font-normal text-leaf-200/70">(negative mentions / week, {data.weeks}w)</span>
        </SectionHeading>
      </div>
      {alerts.length > 0 && (
        <div className="mb-2 space-y-1">
          {alerts.map((a) => (
            <p key={a.aspect} className="rounded-lg border border-chili-500/40 bg-chili-600/20 px-2 py-1 text-xs text-chili-200">
              ⚠ {a.aspect} complaints spiking ↑{a.top_dishes.length > 0 && <> — {a.top_dishes.join(", ")}</>}
            </p>
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-4">
        {data.aspects
          .filter((a) => a.points.some((p) => p.count > 0))
          .map((a) => (
            <div key={a.aspect} className="text-xs text-leaf-200/70">
              <span className={a.alert ? "text-chili-200" : ""}>{a.aspect}</span>
              <div className="mt-1 flex h-8 items-end gap-0.5 rounded bg-leaf-950/60 px-0.5 pt-0.5">
                {a.points.map((p) => (
                  <span
                    key={p.week_start}
                    title={`${p.week_start}: ${p.count}`}
                    className={`w-2 rounded-t ${a.alert ? "bg-chili-500/80" : "bg-brass-500/60"}`}
                    style={{ height: `${Math.max(8, (p.count / max) * 100)}%`, opacity: p.count === 0 ? 0.15 : 1 }}
                  />
                ))}
              </div>
            </div>
          ))}
      </div>
    </div>
  );
}

export function ReviewsTab() {
  const [filter, setFilter] = useState("all");
  const [scoring, setScoring] = useState(false);
  const loadReviews = useCallback(
    () => adminApi<ReviewList>(`/admin/reviews?filter=${filter}&limit=50`),
    [filter],
  );
  const { data, error, refresh, setError } = useLoad(loadReviews);

  const scorePending = async () => {
    setScoring(true);
    try {
      const run = await adminApi<{ scored: number; failed: number; model: string | null }>(
        "/admin/reviews/score-pending",
        { method: "POST" },
      );
      setError("");
      refresh();
      window.alert(`Scored ${run.scored} review(s)${run.model ? ` via ${run.model}` : ""}${run.failed ? `, ${run.failed} failed` : ""}`);
    } catch (e) {
      setError(e instanceof AdminApiError ? e.message : "scoring failed");
    } finally {
      setScoring(false);
    }
  };

  return (
    <div>
      <ErrorBar msg={error} />
      <TrendsPanel />
      <div className="mb-3 flex items-center gap-2">
        <Select tone="dark" className="px-2 py-1" value={filter} onChange={(e) => setFilter(e.target.value)}>
          {["all", "unscored", "negative", "unreplied"].map((f) => (
            <option key={f}>{f}</option>
          ))}
        </Select>
        <Btn variant="gold" size="sm" disabled={scoring || (data?.unscored ?? 0) === 0} onClick={scorePending}>
          {scoring ? "scoring…" : `🤖 score ${data?.unscored ?? 0} pending`}
        </Btn>
        <Btn variant="ghost" size="sm" onClick={refresh}>↻ refresh</Btn>
        {data && <span className="text-xs text-leaf-200/70">{data.total} review(s)</span>}
      </div>
      <div className="space-y-2">
        {(data?.reviews ?? []).map((r) => (
          <ReviewCard key={r.id} review={r} onAction={refresh} onError={setError} />
        ))}
        {data?.reviews.length === 0 && <EmptyState>No reviews here 🎉</EmptyState>}
      </div>
    </div>
  );
}
