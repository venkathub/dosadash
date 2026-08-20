"use client";

import { useCallback, useState } from "react";
import { AdminApiError, adminApi } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

const inputCls =
  "rounded border border-stone-600 bg-stone-900 px-2 py-1 text-sm text-stone-100 placeholder-stone-500";
const btnCls = "rounded bg-amber-500 px-3 py-1 text-sm font-semibold text-stone-900 disabled:opacity-40";

type Coupon = {
  id: number;
  code: string;
  type: "PCT" | "FLAT";
  value: string;
  description: string | null;
  min_subtotal: string | null;
  max_discount: string | null;
  usage_limit: number | null;
  per_user_limit: number | null;
  valid_to: string | null;
  is_active: boolean;
  source: string;
  times_used: number;
};

const EMPTY = {
  code: "",
  type: "PCT" as "PCT" | "FLAT",
  value: "",
  description: "",
  min_subtotal: "",
  max_discount: "",
  usage_limit: "",
  per_user_limit: "",
};

/** Coupon engine (Phase 7): create → activate → customers redeem at
 * checkout. AI-suggested coupons (promo agent) appear here as inactive
 * 🤖 drafts in the same activation flow. */
export function CouponsTab() {
  const loadCoupons = useCallback(() => adminApi<Coupon[]>("/admin/coupons"), []);
  const { data: coupons, error, refresh, setError } = useLoad(loadCoupons);
  const [form, setForm] = useState(EMPTY);
  const [suggesting, setSuggesting] = useState(false);
  const [suggestNote, setSuggestNote] = useState<string | null>(null);

  const act = (fn: () => Promise<unknown>) =>
    fn()
      .then(refresh)
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "action failed"));

  const suggest = async () => {
    setSuggesting(true);
    setSuggestNote(null);
    try {
      const r = await adminApi<{ combos: unknown[]; coupons: unknown[]; skipped: string[]; fallback: boolean }>(
        "/admin/promos/suggest",
        { method: "POST" },
      );
      setSuggestNote(
        `🤖 drafted ${r.combos.length} combo(s) + ${r.coupons.length} coupon(s)` +
          (r.fallback ? " (deterministic fallback)" : "") +
          (r.skipped.length ? ` · skipped: ${r.skipped.join("; ")}` : "") +
          " — review combos on the Combos tab",
      );
      refresh();
    } catch (e) {
      setError(e instanceof AdminApiError ? e.message : "suggestion failed");
    } finally {
      setSuggesting(false);
    }
  };

  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [k]: e.target.value });

  return (
    <div>
      <ErrorBar msg={error} />
      <div className="mb-3 flex items-center gap-3">
        <button className={btnCls} disabled={suggesting} onClick={suggest}>
          {suggesting ? "🤖 Thinking…" : "🤖 Suggest promos"}
        </button>
        {suggestNote && <span className="text-xs text-stone-400">{suggestNote}</span>}
      </div>
      <form
        className="mb-4 flex flex-wrap items-center gap-2 rounded bg-stone-800 p-3"
        onSubmit={(e) => {
          e.preventDefault();
          act(() =>
            adminApi("/admin/coupons", {
              method: "POST",
              body: {
                code: form.code,
                type: form.type,
                value: form.value,
                description: form.description || null,
                min_subtotal: form.min_subtotal || null,
                max_discount: form.max_discount || null,
                usage_limit: form.usage_limit ? Number(form.usage_limit) : null,
                per_user_limit: form.per_user_limit ? Number(form.per_user_limit) : null,
              },
            }),
          ).then(() => setForm(EMPTY));
        }}
      >
        <span className="text-xs uppercase tracking-wide text-stone-400">New coupon</span>
        <input className={`${inputCls} w-28`} placeholder="CODE" required value={form.code} onChange={set("code")} />
        <select className={inputCls} value={form.type} onChange={set("type")}>
          <option value="PCT">% off</option>
          <option value="FLAT">₹ off</option>
        </select>
        <input className={`${inputCls} w-20`} placeholder={form.type === "PCT" ? "%" : "₹"} required value={form.value} onChange={set("value")} />
        <input className={`${inputCls} w-24`} placeholder="min ₹ cart" value={form.min_subtotal} onChange={set("min_subtotal")} />
        {form.type === "PCT" && (
          <input className={`${inputCls} w-24`} placeholder="max ₹ off" value={form.max_discount} onChange={set("max_discount")} />
        )}
        <input className={`${inputCls} w-20`} placeholder="uses" value={form.usage_limit} onChange={set("usage_limit")} />
        <input className={`${inputCls} w-20`} placeholder="per user" value={form.per_user_limit} onChange={set("per_user_limit")} />
        <input className={`${inputCls} w-44`} placeholder="Description" value={form.description} onChange={set("description")} />
        <button className={btnCls}>Create (inactive)</button>
      </form>

      <div className="space-y-2">
        {(coupons ?? []).map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-3 rounded bg-stone-800 p-3 text-sm">
            <span className="font-mono font-bold">{c.code}</span>
            <span>{c.type === "PCT" ? `${parseFloat(c.value)}% off${c.max_discount ? ` (max ₹${parseFloat(c.max_discount)})` : ""}` : `₹${parseFloat(c.value)} off`}</span>
            {c.min_subtotal && <span className="text-stone-400">min ₹{parseFloat(c.min_subtotal)}</span>}
            <span className="text-stone-400">
              used {c.times_used}
              {c.usage_limit ? `/${c.usage_limit}` : ""}
            </span>
            {c.description && <span className="text-xs text-stone-500">{c.description}</span>}
            <span className={`rounded px-2 py-0.5 text-xs ${c.is_active ? "bg-green-800 text-green-200" : "bg-stone-700"}`}>
              {c.is_active ? "ACTIVE" : "INACTIVE"}
              {c.source === "AI_SUGGESTED" ? " · 🤖 AI" : ""}
            </span>
            <span className="ml-auto flex gap-2">
              <button
                className="rounded bg-stone-700 px-2 py-0.5 text-xs"
                onClick={() => act(() => adminApi(`/admin/coupons/${c.id}`, { method: "PATCH", body: { is_active: !c.is_active } }))}
              >
                {c.is_active ? "Deactivate" : "Activate"}
              </button>
              {c.times_used === 0 && (
                <button
                  className="rounded bg-red-900 px-2 py-0.5 text-xs text-red-200"
                  onClick={() => act(() => adminApi(`/admin/coupons/${c.id}`, { method: "DELETE" }))}
                >
                  Delete
                </button>
              )}
            </span>
          </div>
        ))}
        {coupons?.length === 0 && <p className="text-sm text-stone-500">No coupons yet.</p>}
      </div>
    </div>
  );
}
