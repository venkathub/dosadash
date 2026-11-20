/**
 * DosaDash "Tawa Noir" UI primitives — docs/17-ui-tawa-noir-design.md
 * The single source of truth for buttons, inputs, cards, badges, modals,
 * tables and status colors across customer, KDS and admin surfaces.
 * No dependencies — plain Tailwind class composition.
 *
 * One dark surface system: soot panels, cream text, 1px cream hairlines.
 * Ember is reserved for the primary action; ghee marks what is active or
 * needs attention. Variant NAMES are historical (Madras Pop call sites) —
 * read the comment beside each for what it means now.
 */
import React from "react";

export function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

/* ---------------------------------- Button --------------------------------- */

export type BtnVariant =
  | "magenta" // primary CTA (ember)
  | "turmeric" // secondary-primary (cream), the default
  | "indigo"
  | "paper" // default light-surface button
  | "veg"
  | "ghost" // dark-surface quiet outline
  | "danger"
  | "subtle"; // dark-surface quiet fill

export type BtnSize = "sm" | "md" | "lg";

const BTN_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-full border font-display font-bold " +
  "transition-colors duration-100 focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-ghee disabled:cursor-not-allowed disabled:border-cream/10 " +
  "disabled:bg-soot-700 disabled:text-cream-faint";

const BTN_VARIANT: Record<BtnVariant, string> = {
  magenta: "border-transparent bg-ember text-ember-ink hover:bg-ember-hot", // primary
  turmeric: "border-transparent bg-cream text-soot-950 hover:bg-cream/85", // default / secondary
  indigo: "border-cream/20 bg-soot-800 text-cream hover:bg-soot-700",
  paper: "border-cream/20 bg-soot-800 text-cream hover:bg-soot-700",
  veg: "border-transparent bg-basil text-soot-950 hover:bg-basil/85", // confirm
  danger: "border-transparent bg-chili text-soot-950 hover:bg-chili/85",
  ghost: "border-cream/20 bg-transparent text-cream hover:border-ghee/60 hover:text-ghee",
  subtle: "border-cream/15 bg-white/5 text-cream hover:bg-white/10",
};

const BTN_SIZE: Record<BtnSize, string> = {
  sm: "px-2.5 py-1 text-xs",
  md: "px-4 py-1.5 text-sm",
  lg: "px-6 py-2.5 text-base",
};

export function Btn({
  variant = "turmeric",
  size = "md",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: BtnVariant;
  size?: BtnSize;
}) {
  return (
    <button
      className={cx(BTN_BASE, BTN_VARIANT[variant], BTN_SIZE[size], className)}
      {...props}
    />
  );
}

/* ------------------------------- Form controls ------------------------------ */

export type Tone = "light" | "dark";

const FIELD_BASE =
  "rounded-xl border px-3 py-1.5 text-sm outline-none transition-colors duration-100 disabled:opacity-40";

// Two tone names survive from the light/dark split; both are dark now.
const FIELD_TONE: Record<Tone, string> = {
  light: "border-cream/20 bg-soot-950 text-cream placeholder-cream-faint focus:border-ghee",
  dark: "border-cream/20 bg-soot-950 text-cream placeholder-cream-faint focus:border-ghee",
};

export function fieldCls(tone: Tone = "dark", className?: string) {
  return cx(FIELD_BASE, FIELD_TONE[tone], className);
}

export function Input({
  tone = "dark",
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { tone?: Tone }) {
  return <input className={fieldCls(tone, className)} {...props} />;
}

export function Select({
  tone = "dark",
  className,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & { tone?: Tone }) {
  return <select className={fieldCls(tone, className)} {...props} />;
}

export function Textarea({
  tone = "dark",
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { tone?: Tone }) {
  return <textarea className={fieldCls(tone, className)} {...props} />;
}

/* ----------------------------------- Card ---------------------------------- */

const CARD_TONE: Record<Tone, string> = {
  light: "rounded-2xl border border-cream/10 bg-soot-900 text-cream",
  dark: "rounded-2xl border border-cream/10 bg-soot-900 text-cream",
};

export function Card({
  tone = "dark",
  hover = false,
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { tone?: Tone; hover?: boolean }) {
  return (
    <div
      className={cx(
        CARD_TONE[tone],
        hover && "transition-colors duration-150 hover:border-cream/30",
        className,
      )}
      {...props}
    />
  );
}

/* ---------------------------------- Badge ----------------------------------- */

export type BadgeTone =
  | "success"
  | "danger"
  | "warning"
  | "info"
  | "brass" // AI provenance — ember
  | "neutral";

const BADGE_TONE: Record<BadgeTone, string> = {
  success: "border-basil/50 bg-basil/15 text-[#5BD69B]",
  danger: "border-chili/50 bg-chili/15 text-[#FF8B8B]",
  warning: "border-ghee/50 bg-ghee/15 text-ghee",
  info: "border-sky/50 bg-sky/15 text-[#8FC1E9]",
  brass: "border-ember/50 bg-ember/15 text-ember-hot",
  neutral: "border-cream/20 bg-cream/5 text-cream-dim",
};

export function Badge({
  tone = "neutral",
  surface = "dark",
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  tone?: BadgeTone;
  surface?: Tone;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 font-display text-[11px] font-bold uppercase tracking-[0.08em]",
        BADGE_TONE[tone],
        className,
      )}
      {...props}
    />
  );
}

/** One mapping for every status string in the product (orders, POs, QC,
 *  translations, coupons, sentiment, evals…). Unknown → neutral. */
export function statusBadgeTone(status: string): BadgeTone {
  const s = status.toUpperCase();
  if (
    [
      "DELIVERED",
      "APPROVED",
      "ACTIVE",
      "PASS",
      "PASSED",
      "RESOLVED",
      "MATCHED",
      "RECEIVED",
      "POSITIVE",
      "CAPTURED",
      "COMPLETED",
      "READY",
    ].includes(s)
  )
    return "success";
  if (
    [
      "CANCELLED",
      "REFUNDED",
      "REJECTED",
      "FAIL",
      "FAILED",
      "MISMATCH",
      "NEGATIVE",
      "INACTIVE",
    ].includes(s)
  )
    return "danger";
  if (
    [
      "DRAFT",
      "PENDING",
      "PENDING_APPROVAL",
      "PENDING_REVIEW",
      "CHECK",
      "COOKING",
      "MIXED",
      "SUBMITTED",
    ].includes(s)
  )
    return "warning";
  if (["PLACED", "CONFIRMED", "OUT_FOR_DELIVERY", "IN_PROGRESS"].includes(s))
    return "info";
  if (s === "AI_SUGGESTED" || s === "AI_DRAFT") return "brass";
  return "neutral";
}

/* ---------------------------------- Modal ----------------------------------- */

// Lives in its own client file: it needs state, effects and a portal.
export { Field, Modal, ModalHeader, popupField, popupPrimary } from "./Modal";

/* ------------------------------ Headings & chips ---------------------------- */

/** Space Grotesk heading. `kolam` is accepted for old call sites and ignored. */
export function SectionHeading({
  as: Tag = "h2",
  className,
  children,
}: {
  as?: "h1" | "h2" | "h3";
  kolam?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <Tag
      className={cx(
        "font-display font-semibold tracking-tight",
        className,
      )}
    >
      {children}
    </Tag>
  );
}

/** 11px uppercase label row. */
export function Eyebrow({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cx(
        "font-display text-[11px] font-bold uppercase tracking-[0.16em]",
        className ?? "text-ghee",
      )}
      {...props}
    />
  );
}

export function Chip({
  active = false,
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  surface?: Tone;
  active?: boolean;
}) {
  return (
    <button
      className={cx(
        "rounded-full border px-3 py-1 text-xs font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ghee disabled:opacity-40",
        active
          ? "border-cream bg-cream text-soot-950"
          : "border-cream/15 text-cream-dim hover:border-cream/40 hover:text-cream",
        className,
      )}
      {...props}
    />
  );
}

/* ----------------------------- Section furniture ----------------------------- */

/** Divider. Was the Kanchipuram zari stripe; now a hairline (wide) or a short ember rule. */
export function Zari({
  wide = false,
  className,
}: {
  wide?: boolean;
  /** Accepted for old call sites; there is only one surface now. */
  onDark?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cx(wide ? "h-px w-full bg-cream/10" : "h-0.5 w-10 rounded-full bg-ember", className)}
      aria-hidden="true"
    />
  );
}

/** Section label. Was a colour-blocked poster chip; now a quiet eyebrow.
 *  tamil=true drops uppercase/tracking (Latin affordances). */
export function PosterBlock({
  tamil = false,
  tone: _tone,
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  /** Accepted for old call sites; tone no longer changes the look. */
  tone?: "magenta" | "turmeric" | "indigo";
  tamil?: boolean;
}) {
  return (
    <span
      className={cx(
        "font-display text-xs font-semibold text-ghee",
        !tamil && "uppercase tracking-[0.2em]",
        className,
      )}
      {...props}
    />
  );
}

/** FSSAI-style veg / non-veg mark (bordered square + dot). */
export function FssaiMark({ veg, className }: { veg: boolean; className?: string }) {
  return (
    <span
      className={cx("fssai", veg ? "fssai-veg" : "fssai-nonveg", className)}
      role="img"
      aria-label={veg ? "vegetarian" : "non-vegetarian"}
      title={veg ? "Veg" : "Non-veg"}
    />
  );
}

/* ---------------------------------- Tables ----------------------------------- */

export const tableCls = "tnum w-full text-left text-[13px]";
export const theadCls =
  "font-display text-[11px] font-semibold uppercase tracking-[0.12em] text-cream-faint";
export const trCls =
  "border-t border-cream/10 odd:bg-white/[.02] hover:bg-white/[.05]";
export const thCls = "px-2 py-2";
export const tdCls = "px-2 py-1.5";

/* --------------------------------- Feedback ---------------------------------- */

export function ErrorBar({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return (
    <div className="animate-fade-up rounded-xl border border-chili/50 bg-chili/10 px-3 py-2 text-sm font-semibold text-[#FF8B8B]">
      {msg}
    </div>
  );
}

export function EmptyState({
  children,
  surface = "dark",
}: {
  children: React.ReactNode;
  surface?: Tone;
}) {
  return (
    <div
      className={cx(
        "py-8 text-center text-sm",
        surface === "dark" ? "text-cream-dim/80" : "text-cream-faint",
      )}
    >
      {children}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cx(
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-ghee/50 border-t-transparent align-middle",
        className,
      )}
      aria-label="loading"
    />
  );
}
