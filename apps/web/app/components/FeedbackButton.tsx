"use client";

import { useState } from "react";

import { api, ApiError } from "../../lib/api";
import { Btn, Input, Modal, Textarea } from "./ui";

type FeedbackOut = {
  id: number;
  status: string;
  duplicate: boolean;
};

/** 🐞 Report a bug / request a feature — the GUI entry point of the
 * self-healing loop (Phase 13). Anonymous allowed; the api redacts,
 * dedupes, rate-limits, and mirrors to GitHub. Bottom-LEFT so it never
 * collides with the ChatWidget / SupportBox FABs (bottom-right). */
export default function FeedbackButton() {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<"BUG" | "FEATURE">("BUG");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<FeedbackOut | null>(null);

  const reset = () => {
    setKind("BUG");
    setTitle("");
    setDescription("");
    setError(null);
    setDone(null);
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await api<FeedbackOut>("/feedback", {
        method: "POST",
        auth: true, // header only attaches when a token exists — anon works
        body: {
          type: kind,
          title: title.trim(),
          description: description.trim(),
          context: { route: window.location.pathname },
        },
      });
      setDone(created);
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        setError("Too many reports from here — please try again in a minute.");
      } else if (e instanceof ApiError && e.status === 422) {
        setError("Please give a short title (5+ chars) and a few details (10+ chars).");
      } else {
        setError(e instanceof ApiError ? e.message : "Could not send the report.");
      }
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => {
          reset();
          setOpen(true);
        }}
        aria-label="Report a bug or request a feature"
        className="fixed bottom-4 left-4 z-40 rounded-full border-2 border-indigo-900 bg-paper px-3 py-2 font-display text-sm font-bold text-indigo-900 shadow-pop transition-transform hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500"
      >
        🐞
      </button>
    );
  }

  return (
    <Modal tone="light" onClose={() => setOpen(false)} className="w-[22rem] space-y-3 p-6">
      <h2 className="font-display text-lg font-bold text-indigo-900">
        {done ? "Thank you! 🙏" : "Spotted a bug? Want a feature?"}
      </h2>

      {done ? (
        <div className="space-y-3 text-sm text-ink">
          <p>
            {done.duplicate
              ? "Someone already reported this — we've linked your report to the open one."
              : `Tracked as report #${done.id}. Our kitchen's AI engineer is on it.`}
          </p>
          <Btn variant="magenta" className="w-full" onClick={() => setOpen(false)}>
            Close
          </Btn>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex gap-2">
            {(["BUG", "FEATURE"] as const).map((k) => (
              <button
                key={k}
                onClick={() => setKind(k)}
                className={`flex-1 rounded-lg border-2 px-2 py-1.5 font-display text-xs font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500 ${
                  kind === k
                    ? "border-indigo-900 bg-turmeric-500 text-indigo-900 shadow-pop-xs"
                    : "border-indigo-900/40 bg-paper text-muted"
                }`}
              >
                {k === "BUG" ? "🐞 Bug" : "✨ Feature"}
              </button>
            ))}
          </div>
          <Input
            tone="light"
            placeholder={kind === "BUG" ? "What broke? (short title)" : "What would you love?"}
            value={title}
            maxLength={120}
            onChange={(e) => setTitle(e.target.value)}
          />
          <Textarea
            tone="light"
            rows={4}
            maxLength={2000}
            placeholder={
              kind === "BUG"
                ? "What happened, and what did you expect? (please don't include phone numbers)"
                : "Describe the feature and why it would help."
            }
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          {error && <p className="text-xs font-semibold text-chili">{error}</p>}
          <Btn
            variant="magenta"
            className="w-full"
            disabled={busy || title.trim().length < 5 || description.trim().length < 10}
            onClick={submit}
          >
            {busy ? "Sending…" : "Send report"}
          </Btn>
        </div>
      )}
    </Modal>
  );
}
