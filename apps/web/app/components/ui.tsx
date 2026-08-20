/**
 * DosaDash "Heritage Luxe" UI primitives — docs/12-ui-premium-design.md
 * The single source of truth for buttons, inputs, cards, badges, modals,
 * tables and status colors across customer, KDS and admin surfaces.
 * No dependencies — plain Tailwind class composition.
 */
import React from "react";

export function cx(...parts: Array<string | false | null | undefined>) {
  return parts.filter(Boolean).join(" ");
}

/* ---------------------------------- Button --------------------------------- */

export type BtnVariant = "gold" | "leaf" | "ghost" | "danger" | "subtle";
export type BtnSize = "sm" | "md" | "lg";

const BTN_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-lg font-semibold transition-colors duration-150 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brass-500 " +
  "disabled:cursor-not-allowed disabled:opacity-40";

const BTN_VARIANT: Record<BtnVariant, string> = {
  gold: "btn-gold shadow-card hover:shadow-lift",
  leaf: "bg-leaf-700 text-leaf-100 hover:bg-leaf-600",
  ghost:
    "border border-leaf-600 text-leaf-200 hover:border-brass-400 hover:text-brass-300",
  danger: "border border-chili-500/60 text-chili-500 hover:bg-chili-500 hover:text-white",
  subtle: "bg-black/20 text-leaf-200 hover:bg-black/30",
};

const BTN_SIZE: Record<BtnSize, string> = {
  sm: "px-2.5 py-1 text-xs",
  md: "px-4 py-1.5 text-sm",
  lg: "px-6 py-2.5 text-base",
};

export function Btn({
  variant = "gold",
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
  "rounded-lg px-3 py-1.5 text-sm outline-none transition-colors duration-150 " +
  "focus:ring-2 focus:ring-brass-500 disabled:opacity-40";

const FIELD_TONE: Record<Tone, string> = {
  light:
    "border border-cream-300 bg-cream-50 text-ink-900 placeholder-ink-400 focus:border-brass-500",
  dark: "border border-leaf-600 bg-leaf-950 text-leaf-100 placeholder-leaf-500 focus:border-brass-500",
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
  light: "rounded-xl border border-cream-300/70 bg-cream-50 shadow-card",
  dark: "rounded-xl border-t border-white/5 bg-leaf-900",
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
        hover &&
          "transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lift",
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
  | "brass"
  | "neutral";

const BADGE_TONE: Record<BadgeTone, string> = {
  success: "bg-veg-600/25 text-veg-200 border border-veg-600/40",
  danger: "bg-chili-600/25 text-chili-200 border border-chili-600/40",
  warning: "bg-turmeric-500/20 text-turmeric-200 border border-turmeric-500/40",
  info: "bg-info-500/20 text-info-200 border border-info-500/40",
  brass: "bg-brass-500/15 text-brass-300 border border-brass-500/40",
  neutral: "bg-white/5 text-leaf-200 border border-white/10",
};

/** Same tones re-mixed for light (cream) surfaces. */
const BADGE_TONE_LIGHT: Record<BadgeTone, string> = {
  success: "bg-veg-200 text-veg-600 border border-veg-500/30",
  danger: "bg-chili-200 text-chili-600 border border-chili-500/30",
  warning: "bg-turmeric-200 text-ink-900 border border-turmeric-500/40",
  info: "bg-info-200 text-ink-900 border border-info-500/30",
  brass: "bg-brass-300/40 text-brass-600 border border-brass-500/40",
  neutral: "bg-cream-200 text-ink-600 border border-cream-300",
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
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold",
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
      className="fixed inset-0 z-50 flex items-center justify-center bg-leaf-950/60 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className={cx(
          "animate-fade-up max-h-[90vh] overflow-y-auto rounded-2xl shadow-modal",
          tone === "light"
            ? "bg-cream-50 text-ink-900"
            : "border-t border-white/5 bg-leaf-900 text-leaf-100",
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

/** Fraunces heading + kolam dot divider. */
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
        "font-display font-semibold tracking-tight",
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
        "text-[11px] font-semibold uppercase tracking-[0.14em]",
        className ?? "text-brass-300/80",
      )}
      {...props}
    />
  );
}

export function Chip({
  surface = "light",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { surface?: Tone }) {
  return (
    <button
      className={cx(
        "rounded-full border px-3 py-1 text-xs transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brass-500 disabled:opacity-40",
        surface === "light"
          ? "border-cream-300 bg-cream-50 text-ink-900 hover:border-brass-500 hover:bg-cream-200"
          : "border-leaf-600 bg-leaf-800 text-leaf-100 hover:border-brass-400 hover:text-brass-300",
        className,
      )}
      {...props}
    />
  );
}

/* ---------------------------------- Tables ----------------------------------- */

export const tableCls = "tnum w-full text-left text-[13px]";
export const theadCls =
  "text-[11px] font-semibold uppercase tracking-[0.14em] text-brass-300/70";
export const trCls = "border-t border-white/5 odd:bg-white/[.02] hover:bg-white/[.04]";
export const thCls = "px-2 py-2";
export const tdCls = "px-2 py-1.5";

/* --------------------------------- Feedback ---------------------------------- */

export function ErrorBar({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return (
    <div className="animate-fade-up rounded-lg border border-chili-500/40 bg-chili-600/20 px-3 py-2 text-sm text-chili-200">
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
        surface === "dark" ? "text-leaf-200/70" : "text-ink-400",
      )}
    >
      <div className="mb-1 tracking-[0.35em] text-brass-400/60">·∙•∙·</div>
      {children}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cx(
        "inline-block h-4 w-4 animate-spin rounded-full border-2 border-brass-500 border-t-transparent align-middle",
        className,
      )}
      aria-label="loading"
    />
  );
}
