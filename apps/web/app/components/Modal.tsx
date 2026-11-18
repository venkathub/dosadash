"use client";

import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * Centred dialog over a dimmed page.
 *
 * Rendered through a PORTAL into <body>. A `position: fixed` element is
 * positioned against the nearest ancestor that has a backdrop-filter,
 * filter or transform — not the viewport. The sticky app bar has
 * `backdrop-blur`, so a modal opened from a button inside it centred itself
 * on the 56px bar and was cut off. In <body> no ancestor can capture it.
 *
 * Responsive by construction: the overlay scrolls, so a dialog taller than
 * the screen (small phone, landscape, on-screen keyboard) is still fully
 * reachable; give the panel `w-full max-w-*`, never a fixed width.
 */
export function Modal({
  onClose,
  className,
  children,
}: {
  onClose?: () => void;
  /** Kept so existing call sites compile; every modal is dark now. */
  tone?: "light" | "dark";
  className?: string;
  children: React.ReactNode;
}) {
  const [mounted, setMounted] = useState(false);
  // Latest onClose without re-running the effect (callers pass a new function each render).
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    setMounted(true);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden"; // the page behind does not scroll
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current?.();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  if (!mounted) return null;
  return createPortal(
    <div className="fixed inset-0 z-[70] overflow-y-auto overscroll-contain bg-black/70" onClick={onClose}>
      <div className="flex min-h-full items-center justify-center p-4">
        <div
          role="dialog"
          aria-modal="true"
          className={[
            "animate-fade-up rounded-3xl border border-cream/15 bg-soot-900 text-cream shadow-noir",
            className,
          ]
            .filter(Boolean)
            .join(" ")}
          onClick={(e) => e.stopPropagation()}
        >
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** Standard popup header: eyebrow, title, optional lead line, and a close button. */
export function ModalHeader({
  eyebrow,
  title,
  children,
  onClose,
  titleId,
}: {
  eyebrow?: string;
  title: React.ReactNode;
  /** Lead paragraph under the title. */
  children?: React.ReactNode;
  onClose?: () => void;
  titleId?: string;
}) {
  return (
    <header className="flex items-start gap-4">
      <div className="min-w-0 flex-1">
        {eyebrow && (
          <p className="font-display text-xs font-semibold uppercase tracking-[0.2em] text-ghee">{eyebrow}</p>
        )}
        <h2 id={titleId} className="mt-1.5 font-display text-2xl font-semibold leading-tight tracking-[-0.02em] text-cream">
          {title}
        </h2>
        {children && <p className="mt-2 text-sm text-cream-dim">{children}</p>}
      </div>
      {onClose && (
        <button
          aria-label="Close"
          onClick={onClose}
          className="grid h-9 w-9 shrink-0 place-items-center rounded-full border border-cream/15 text-cream-dim transition-colors hover:border-cream/40 hover:text-cream"
        >
          ✕
        </button>
      )}
    </header>
  );
}

/** Labelled form row with an optional right-aligned note (counter, hint). */
export function Field({
  label,
  htmlFor,
  note,
  children,
}: {
  label: string;
  htmlFor: string;
  note?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <label htmlFor={htmlFor} className="text-[13px] font-semibold text-cream">
          {label}
        </label>
        {note && <span className="tnum text-xs text-cream-faint">{note}</span>}
      </div>
      {children}
    </div>
  );
}

/** Text input / textarea look for popups. */
export const popupField =
  "w-full rounded-2xl border border-cream/15 bg-soot-950 px-4 text-[15px] text-cream outline-none transition-colors placeholder:text-cream-faint focus:border-ghee";
/** Primary action in a popup. */
export const popupPrimary =
  "inline-flex h-12 w-full items-center justify-center rounded-full bg-ember font-display text-base font-bold text-ember-ink shadow-ember transition hover:bg-ember-hot active:translate-y-px disabled:cursor-not-allowed disabled:bg-soot-700 disabled:text-cream-faint disabled:shadow-none";
