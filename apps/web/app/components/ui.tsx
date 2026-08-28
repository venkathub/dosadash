/**
 * DosaDash "Madras Pop" UI primitives — docs/13-ui-madras-pop-design.md
 * (normative token source: design/madras-pop/tokens.css)
 * The single source of truth for buttons, inputs, cards, badges, modals,
 * tables and status colors across customer, KDS and admin surfaces.
 * No dependencies — plain Tailwind class composition.
 *
 * Signature construction: 2px ink borders + hard offset shadows (shadow-pop*),
 * Space Grotesk display on buttons/headings/badges. Never soften the shadows.
 */
import React from "react";

export function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

/* ---------------------------------- Button --------------------------------- */

export type BtnVariant =
  | "magenta" // primary CTA
  | "turmeric" // ADD / secondary-primary (default)
  | "indigo"
  | "paper" // default light-surface button
  | "veg"
  | "ghost" // dark-surface quiet outline
  | "danger"
  | "subtle"; // dark-surface quiet fill

export type BtnSize = "sm" | "md" | "lg";

const BTN_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border-2 font-display font-bold " +
  "transition-colors duration-100 focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-magenta-500 disabled:cursor-not-allowed disabled:border-faint " +
  "disabled:bg-sand-300 disabled:text-faint disabled:shadow-none";

const BTN_VARIANT: Record<BtnVariant, string> = {
  magenta: "border-indigo-900 bg-magenta-500 text-white shadow-pop-sm hover:bg-magenta-400",
  turmeric:
    "border-indigo-900 bg-turmeric-500 text-indigo-900 shadow-pop-sm hover:bg-turmeric-400",
  indigo: "border-indigo-900 bg-indigo-900 text-white shadow-pop-sm hover:bg-indigo-700",
  paper: "border-indigo-900 bg-paper text-indigo-900 shadow-pop-sm hover:bg-indigo-100",
  veg: "border-indigo-900 bg-veg text-white shadow-pop-sm hover:opacity-90",
  danger: "border-indigo-900 bg-chili text-white shadow-pop-sm hover:opacity-90",
  ghost:
    "border-indigo-600 bg-transparent text-indigo-100 shadow-none hover:border-turmeric-400 hover:text-turmeric-400",
  subtle:
    "border-indigo-600 bg-white/5 text-indigo-100 shadow-none hover:bg-white/10",
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
  "rounded-lg border-2 px-3 py-1.5 text-sm outline-none transition-shadow duration-100 disabled:opacity-40";

const FIELD_TONE: Record<Tone, string> = {
  light:
    "border-indigo-900 bg-paper text-ink placeholder-faint focus:shadow-pop-magenta-sm",
  dark: "border-indigo-600 bg-indigo-950 text-indigo-100 placeholder-indigo-300 focus:border-turmeric-400",
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
  // light poster card: paper + ink border + hard shadow
  light: "rounded-xl border-2 border-indigo-900 bg-paper shadow-pop",
  // calmer dark panel (admin): no shadow — tokens.css .card-panel-dark
  dark: "rounded-xl border-2 border-indigo-700 bg-indigo-900 text-indigo-100",
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
        hover && "transition-transform duration-150 hover:-translate-y-0.5",
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
  | "brass" // AI provenance — renders magenta in Madras Pop
  | "neutral";

/** Dark-surface palette (tokens.css .badge-dk-*). */
const BADGE_TONE: Record<BadgeTone, string> = {
  success: "border-[#2FA36B] bg-veg/20 text-[#5BD69B]",
  danger: "border-chili bg-chili/20 text-[#FF8B8B]",
  warning: "border-turmeric-600 bg-turmeric-500/15 text-turmeric-400",
  info: "border-sky bg-sky/20 text-[#8FC1E9]",
  brass: "border-magenta-500 bg-magenta-500/20 text-magenta-400",
  neutral: "border-indigo-600 bg-indigo-200/10 text-indigo-200",
};

/** Same tones re-mixed for light (paper/offwhite) surfaces (tokens.css .badge-*). */
const BADGE_TONE_LIGHT: Record<BadgeTone, string> = {
  success: "border-veg bg-veg-100 text-veg",
  danger: "border-chili bg-chili-100 text-chili",
  warning: "border-[#8A6A03] bg-warn-100 text-[#8A6A03]",
  info: "border-sky bg-sky-100 text-sky",
  brass: "border-magenta-600 bg-magenta-100 text-magenta-600",
  neutral: "border-muted bg-sand-200 text-muted",
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
  const map = surface === "light" ? BADGE_TONE_LIGHT : BADGE_TONE;
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-md border-[1.5px] px-2 py-0.5 font-display text-[11px] font-bold uppercase tracking-[0.08em]",
        map[tone],
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

export function Modal({
  onClose,
  tone = "light",
  className,
  children,
}: {
  onClose?: () => void;
  tone?: Tone;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-indigo-950/70 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={cx(
          "animate-fade-up max-h-[90vh] overflow-y-auto rounded-2xl border-2",
          tone === "light"
            ? "border-indigo-900 bg-paper text-ink shadow-pop"
            : "border-indigo-700 bg-indigo-900 text-indigo-100 shadow-pop-dark",
          className,
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}

/* ------------------------------ Headings & chips ---------------------------- */

/** Space Grotesk heading; kolam=true renders the zari-stripe underline. */
export function SectionHeading({
  as: Tag = "h2",
  kolam = true,
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
        "font-display font-bold tracking-tight",
        kolam && "kolam",
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
        className ?? "text-turmeric-400",
      )}
      {...props}
    />
  );
}

export function Chip({
  surface = "light",
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
        "rounded-full border-2 px-3 py-1 text-xs font-semibold transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500 disabled:opacity-40",
        active
          ? "border-indigo-900 bg-indigo-900 text-turmeric-400"
          : surface === "light"
            ? "border-indigo-900 bg-paper text-ink hover:bg-turmeric-100"
            : "border-indigo-600 bg-indigo-900 text-indigo-100 hover:border-turmeric-400 hover:text-turmeric-400",
        className,
      )}
      {...props}
    />
  );
}

/* --------------------------- Madras Pop signatures --------------------------- */

/** Indigo ticker strip under the customer header — scrolling marquee. */
export function Ticker({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cx("ticker", className)} aria-hidden="true" {...props}>
      {/* Text duplicated so translateX(-50%) creates a seamless scroll loop. */}
      <span className="ticker__inner">
        <span>{children}</span>
        <span aria-hidden="true">{children}</span>
      </span>
    </div>
  );
}

/** Kanchipuram zari stripe section divider. */
export function Zari({
  wide = false,
  onDark = false,
  className,
}: {
  wide?: boolean;
  onDark?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cx("zari", wide && "zari-wide", onDark && "zari-on-dark", className)}
      aria-hidden="true"
    />
  );
}

/** Color-blocked uppercase section header. tamil=true drops uppercase/tracking. */
export function PosterBlock({
  tone = "magenta",
  tamil = false,
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  tone?: "magenta" | "turmeric" | "indigo";
  tamil?: boolean;
}) {
  return (
    <span
      className={cx(
        "poster-block",
        tone === "turmeric" && "poster-block-turmeric",
        tone === "indigo" && "poster-block-indigo",
        tamil && "poster-block-tamil",
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
  "font-display text-[10.5px] font-bold uppercase tracking-[0.12em] text-turmeric-400";
export const trCls =
  "border-t border-indigo-800 odd:bg-white/[.02] hover:bg-white/[.05]";
export const thCls = "px-2 py-2";
export const tdCls = "px-2 py-1.5";

/* --------------------------------- Feedback ---------------------------------- */

export function ErrorBar({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return (
    <div className="animate-fade-up rounded-lg border-2 border-chili bg-chili-100 px-3 py-2 text-sm font-semibold text-chili">
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
        surface === "dark" ? "text-indigo-200/80" : "text-faint",
      )}
    >
      <div className="mx-auto mb-2 zari" aria-hidden="true" />
      {children}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cx(
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-turmeric-500 border-t-transparent align-middle",
        className,
      )}
      aria-label="loading"
    />
  );
}
