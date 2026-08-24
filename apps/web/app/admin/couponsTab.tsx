"use client";

import { useCallback, useState } from "react";
import { Badge, Btn, EmptyState, Eyebrow, Input, Select } from "../components/ui";
import { AdminApiError, adminApi } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

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
        <Btn variant="turmeric" size="sm" disabled={suggesting} onClick={suggest}>
          {suggesting ? "🤖 Thinking…" : "🤖 Suggest promos"}
        </Btn>
        {suggestNote && <span className="ai-meta">{suggestNote}</span>}
      </div>
      <form
        className="mb-4 flex flex-wrap items-center gap-2 rounded-lg bg-indigo-800 p-3"
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
        <Eyebrow>New coupon</Eyebrow>
        <Input tone="dark" className="w-28 px-2 py-1 font-mono" placeholder="CODE" required value={form.code} onChange={set("code")} />
        <Select tone="dark" className="px-2 py-1" value={form.type} onChange={set("type")}>
          <option value="PCT">% off</option>
          <option value="FLAT">₹ off</option>
        </Select>
        <Input tone="dark" className="w-20 px-2 py-1" placeholder={form.type === "PCT" ? "%" : "₹"} required value={form.value} onChange={set("value")} />
        <Input tone="dark" className="w-24 px-2 py-1" placeholder="min ₹ cart" value={form.min_subtotal} onChange={set("min_subtotal")} />
        {form.type === "PCT" && (
          <Input tone="dark" className="w-24 px-2 py-1" placeholder="max ₹ off" value={form.max_discount} onChange={set("max_discount")} />
        )}
        <Input tone="dark" className="w-20 px-2 py-1" placeholder="uses" value={form.usage_limit} onChange={set("usage_limit")} />
        <Input tone="dark" className="w-20 px-2 py-1" placeholder="per user" value={form.per_user_limit} onChange={set("per_user_limit")} />
        <Input tone="dark" className="w-44 px-2 py-1" placeholder="Description" value={form.description} onChange={set("description")} />
        <Btn variant="turmeric" size="sm">Create (inactive)</Btn>
      </form>

      <div className="space-y-2">
        {(coupons ?? []).map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-3 rounded-lg bg-indigo-800 p-3 text-sm">
            <span className="font-mono font-bold text-turmeric-400">{c.code}</span>
            <span className="tnum">{c.type === "PCT" ? `${parseFloat(c.value)}% off${c.max_discount ? ` (max ₹${parseFloat(c.max_discount)})` : ""}` : `₹${parseFloat(c.value)} off`}</span>
            {c.min_subtotal && <span className="tnum text-indigo-200/70">min ₹{parseFloat(c.min_subtotal)}</span>}
            <span className="tnum text-indigo-200/70">
              used {c.times_used}
              {c.usage_limit ? `/${c.usage_limit}` : ""}
            </span>
            {c.description && <span className="text-xs text-indigo-200/60">{c.description}</span>}
            <Badge tone={c.is_active ? "success" : "danger"}>{c.is_active ? "ACTIVE" : "INACTIVE"}</Badge>
            {c.source === "AI_SUGGESTED" && <span className="ai-meta">🤖 AI</span>}
            <span className="ml-auto flex gap-2">
              <Btn
                variant={c.is_active ? "ghost" : "turmeric"}
                size="sm"
                onClick={() => act(() => adminApi(`/admin/coupons/${c.id}`, { method: "PATCH", body: { is_active: !c.is_active } }))}
              >
                {c.is_active ? "Deactivate" : "Activate"}
              </Btn>
              {c.times_used === 0 && (
                <Btn
                  variant="danger"
                  size="sm"
                  onClick={() => act(() => adminApi(`/admin/coupons/${c.id}`, { method: "DELETE" }))}
                >
                  Delete
                </Btn>
              )}
            </span>
          </div>
        ))}
        {coupons?.length === 0 && <EmptyState>No coupons yet.</EmptyState>}
      </div>
    </div>
  );
}
