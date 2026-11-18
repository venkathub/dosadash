"use client";

import { useState } from "react";

import { api, ApiError } from "../../lib/api";
import { Btn, Field, Modal, ModalHeader, popupField, popupPrimary } from "./ui";

type FeedbackOut = {
  id: number;
  status: string;
  duplicate: boolean;
};

type Kind = "BUG" | "FEATURE";

/** What happens to a report, in the order it happens (docs/14 self-healing loop). */
const LOOP = ["You report", "AI triages", "Approved", "Fix ships", "Checked live"];

/** 🐞 Report a bug / request a feature — the GUI entry point of the
 * self-healing loop (Phase 13). Anonymous allowed; the api redacts,
 * dedupes, rate-limits, and mirrors to GitHub.
 *
 * Three placements, one form:
 *  - "card"  the suggestion box at the foot of the menu: says what the loop
 *            does and offers both kinds of report (customer home)
 *  - "icon"  a quiet header button, always reachable while the bar is sticky
 *  - "fab"   bottom-left floating button, for pages with no better home
 */
export default function FeedbackButton({ variant = "fab" }: { variant?: "fab" | "icon" | "card" }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<Kind>("BUG");
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

  const start = (k: Kind) => {
    reset();
    setKind(k);
    setOpen(true);
  };

  const trigger =
    variant === "card" ? (
      <section
        aria-labelledby="feedback-title"
        className="relative mt-6 overflow-hidden rounded-3xl border border-cream/10 bg-soot-900 p-5 sm:p-6"
      >
        <div aria-hidden="true" className="absolute -right-16 -top-20 h-52 w-52 rounded-full bg-ember opacity-[0.14] blur-3xl" />
        <div className="relative">
          <p className="font-display text-xs font-semibold uppercase tracking-[0.2em] text-ghee">The suggestion box</p>
          <h2 id="feedback-title" className="mt-2 font-display text-2xl font-semibold tracking-[-0.02em] text-cream">
            Spotted a bug? Want a feature?
          </h2>
          <p className="mt-2 max-w-[60ch] text-sm text-cream-dim">
            Tell the kitchen. Your note becomes a tracked report and an AI engineer reads it. Small, low-risk bug
            fixes go straight through; anything bigger waits for the owner&apos;s approval. Every fix is checked on
            the live site after it ships.
          </p>
          {/* the loop, as a little pass rail */}
          <ol className="mt-4 flex flex-wrap items-center gap-x-1 gap-y-2 text-xs text-cream-dim">
            {LOOP.map((step, i) => (
              <li key={step} className="flex items-center gap-1">
                <span className="flex items-center gap-1.5 rounded-full border border-cream/15 py-1 pl-1 pr-2.5">
                  <span className="grid h-5 w-5 place-items-center rounded-full bg-soot-700 font-display text-[11px] font-semibold text-cream">
                    {i + 1}
                  </span>
                  {step}
                </span>
                {i < LOOP.length - 1 && <span aria-hidden="true" className="text-cream-faint">→</span>}
              </li>
            ))}
          </ol>
          <div className="mt-5 flex flex-wrap gap-2.5">
            <Btn variant="turmeric" size="lg" onClick={() => start("BUG")}>
              🐞 Report a bug
            </Btn>
            <Btn variant="ghost" size="lg" onClick={() => start("FEATURE")}>
              ✨ Suggest a feature
            </Btn>
          </div>
          <p className="mt-3 text-xs text-cream-faint">No login needed. Phone numbers are removed before it is stored.</p>
        </div>
      </section>
    ) : variant === "icon" ? (
      // Highlighted on purpose: a ghee pill with a live dot. It is the one
      // control in the bar that is an invitation rather than navigation.
      // Icon-only below sm, hidden on the very narrowest phones where the bar is full.
      <button
        onClick={() => start("BUG")}
        aria-label="Report a bug or request a feature"
        title="Spotted a bug? Want a feature?"
        className="group relative flex h-[34px] items-center gap-1.5 rounded-full border border-ghee/60 bg-ghee/10 px-2.5 text-[13px] font-semibold text-ghee transition-colors duration-150 hover:bg-ghee hover:text-soot-950 max-[379px]:hidden sm:px-3"
      >
        <span aria-hidden="true">🐞</span>
        <span className="max-sm:hidden">Feedback</span>
        <span aria-hidden="true" className="absolute -right-0.5 -top-0.5 flex h-2.5 w-2.5">
          <span className="animate-pulse-soft absolute inline-flex h-full w-full rounded-full bg-ember" />
        </span>
      </button>
    ) : (
      <button
        onClick={() => start("BUG")}
        aria-label="Report a bug or request a feature"
        title="Spotted a bug? Want a feature?"
        className="fixed bottom-4 left-4 z-40 grid h-11 w-11 place-items-center rounded-full border border-cream/15 bg-soot-900 transition-colors hover:border-ghee focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ghee"
      >
        <span aria-hidden="true">🐞</span>
      </button>
    );

  if (!open) return trigger;

  return (
    <>
      {trigger}
    <Modal onClose={() => setOpen(false)} className="w-full max-w-md p-6 sm:p-7">
      {done ? (
        <div className="text-center">
          <div className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-basil/15 text-2xl text-basil" aria-hidden="true">
            ✓
          </div>
          <h2 className="mt-4 font-display text-2xl font-semibold tracking-[-0.02em] text-cream">
            {done.duplicate ? "Already on the list" : "Thank you, it is in"}
          </h2>
          <p className="mx-auto mt-2 max-w-[38ch] text-sm text-cream-dim" role="status">
            {done.duplicate
              ? "Someone reported this before you, so yours is linked to the open report."
              : `Tracked as report #${done.id}. An AI engineer reads it next.`}
          </p>
          <ol className="mt-5 flex flex-wrap justify-center gap-1.5 text-xs">
            {LOOP.map((step, i) => (
              <li
                key={step}
                className={`rounded-full border px-2.5 py-1 ${
                  i === 0 ? "border-basil/50 bg-basil/15 text-basil" : "border-cream/15 text-cream-faint"
                }`}
              >
                {i === 0 ? "✓ " : ""}
                {step}
              </li>
            ))}
          </ol>
          <button className={`${popupPrimary} mt-6`} onClick={() => setOpen(false)}>
            Back to the menu
          </button>
        </div>
      ) : (
        <div className="space-y-5">
          <ModalHeader eyebrow="The suggestion box" title="Spotted a bug? Want a feature?" onClose={() => setOpen(false)}>
            An AI engineer reads every report. No login needed.
          </ModalHeader>

          <div role="radiogroup" aria-label="What kind of report" className="grid grid-cols-2 gap-2.5">
            {(
              [
                ["BUG", "🐞", "Something broke", "A bug or a glitch"],
                ["FEATURE", "✨", "I have an idea", "A feature you want"],
              ] as const
            ).map(([k, icon, label, hint]) => (
              <button
                key={k}
                role="radio"
                aria-checked={kind === k}
                onClick={() => setKind(k)}
                className={`rounded-2xl border p-3.5 text-left transition-colors ${
                  kind === k ? "border-ember bg-ember/10" : "border-cream/15 hover:border-cream/40"
                }`}
              >
                <span className="text-xl" aria-hidden="true">
                  {icon}
                </span>
                <span className="mt-1.5 block font-display text-[15px] font-semibold text-cream">{label}</span>
                <span className="block text-xs text-cream-dim">{hint}</span>
              </button>
            ))}
          </div>

          <Field label="Short title" htmlFor="fb-title" note={`${title.length}/120`}>
            <input
              id="fb-title"
              className={`${popupField} h-12`}
              placeholder={kind === "BUG" ? "What broke?" : "What would you love?"}
              value={title}
              maxLength={120}
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>
          <Field label="Details" htmlFor="fb-details" note={`${description.length}/2000`}>
            <textarea
              id="fb-details"
              rows={4}
              maxLength={2000}
              className={`${popupField} resize-y py-3`}
              placeholder={
                kind === "BUG"
                  ? "What happened, and what did you expect?"
                  : "Describe the feature and why it would help."
              }
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </Field>

          {error && (
            <p className="rounded-2xl border border-chili/50 bg-chili/10 px-3.5 py-2.5 text-sm font-semibold text-[#FF8B8B]" role="alert">
              {error}
            </p>
          )}
          <div>
            <button
              className={popupPrimary}
              disabled={busy || title.trim().length < 5 || description.trim().length < 10}
              onClick={submit}
            >
              {busy ? "Sending…" : "Send report"}
            </button>
            <p className="mt-2.5 text-center text-xs text-cream-faint">
              {title.trim().length < 5 || description.trim().length < 10
                ? "Add a title (5+ characters) and a few details (10+) to send."
                : "Please leave out phone numbers. Any that slip in are removed before storage."}
            </p>
          </div>
        </div>
      )}
    </Modal>
    </>
  );
}
