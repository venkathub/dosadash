"use client";

/** Inventory tab (Phase 6): agent-drafted purchase orders (approve/receive),
 *  on-demand agent runs, and the wastage log. */

import { useCallback, useState } from "react";
import { AdminApiError, adminApi } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

const inputCls =
  "rounded bg-stone-700 px-2 py-1 text-sm text-stone-100 placeholder-stone-400 outline-none focus:ring-1 focus:ring-amber-400";
const btnCls =
  "rounded bg-amber-500 px-2 py-1 text-xs font-semibold text-stone-900 hover:bg-amber-400 disabled:opacity-40";
const ghostBtnCls =
  "rounded border border-stone-600 px-2 py-1 text-xs text-stone-300 hover:border-amber-400 hover:text-amber-300";

type POItem = {
  ingredient_id: number;
  ingredient_name: string;
  unit: string;
  qty: string;
  unit_cost: string | null;
  reason: string | null;
};

type PO = {
  id: number;
  supplier_id: number | null;
  supplier_name: string | null;
  status: string;
  source: string;
  rationale: string | null;
  coverage_days: number;
  expected_cost: string | null;
  model: string | null;
  prompt_version: string | null;
  created_at: string;
};

type PODetail = PO & { items: POItem[] };

type Wastage = {
  id: number;
  ingredient_id: number;
  ingredient_name: string;
  unit: string;
  qty: string;
  reason: string;
  note: string | null;
  stock_after: string;
  at: string;
};

type Ingredient = { id: number; name: string; unit: string; stock_qty: string };

const STATUS_COLORS: Record<string, string> = {
  PENDING_APPROVAL: "bg-amber-600/60 text-amber-100",
  APPROVED: "bg-emerald-700/60 text-emerald-100",
  RECEIVED: "bg-stone-600 text-stone-200",
  REJECTED: "bg-red-900/60 text-red-200",
  CANCELLED: "bg-stone-700 text-stone-400",
  DRAFT: "bg-stone-700 text-stone-300",
};

export function InventoryTab() {
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const loadPos = useCallback(
    () => adminApi<PO[]>(`/admin/purchase-orders${status ? `?status=${status}` : ""}`),
    [status],
  );
  const { data: pos, error, refresh, setError } = useLoad(loadPos);
  const [open, setOpen] = useState<Record<number, PODetail>>({});

  const act = (fn: () => Promise<unknown>) =>
    fn()
      .then(() => {
        setOpen({});
        refresh();
      })
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "action failed"));

  const toggle = async (id: number) => {
    if (open[id]) {
      setOpen(({ [id]: _gone, ...rest }) => rest);
      return;
    }
    try {
      const detail = await adminApi<PODetail>(`/admin/purchase-orders/${id}`);
      setOpen((o) => ({ ...o, [id]: detail }));
    } catch (e) {
      setError(e instanceof AdminApiError ? e.message : "load failed");
    }
  };

  const draftNow = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await adminApi<PODetail[]>("/admin/purchase-orders/draft-now", { method: "POST" });
      if (created.length === 0) setError("Agent found nothing to reorder (or open drafts already cover it).");
      refresh();
    } catch (e) {
      setError(e instanceof AdminApiError ? e.message : "draft failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <ErrorBar msg={error} />
      <div className="flex items-center gap-2">
        <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all statuses</option>
          {Object.keys(STATUS_COLORS).map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <button className={ghostBtnCls} onClick={refresh}>↻ refresh</button>
        <button className={btnCls} disabled={busy} onClick={draftNow}>
          {busy ? "🤖 drafting…" : "🤖 Run inventory agent now"}
        </button>
      </div>

      <div className="space-y-2">
        {(pos ?? []).map((po) => (
          <div key={po.id} className="rounded bg-stone-800 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <button className="font-mono text-amber-300" onClick={() => toggle(po.id)}>
                {open[po.id] ? "▾" : "▸"} PO #{po.id}
              </button>
              <span className={`rounded px-2 py-0.5 text-xs ${STATUS_COLORS[po.status] ?? "bg-stone-700"}`}>{po.status}</span>
              <span className="text-stone-300">{po.supplier_name ?? "Unassigned supplier"}</span>
              <span className="text-xs text-stone-400">
                {po.source === "AGENT" ? `🤖 ${po.model ?? "agent"} · ${po.prompt_version ?? ""}` : "manual"} · {po.coverage_days}d cover
              </span>
              {po.expected_cost && <span className="ml-auto font-semibold">≈ ₹{Number(po.expected_cost).toFixed(0)}</span>}
              {po.status === "PENDING_APPROVAL" && (
                <>
                  <button className={btnCls} onClick={() => act(() => adminApi(`/admin/purchase-orders/${po.id}/approve`, { method: "POST" }))}>
                    ✅ approve
                  </button>
                  <button
                    className={`${ghostBtnCls} border-red-500 text-red-300`}
                    onClick={() => act(() => adminApi(`/admin/purchase-orders/${po.id}/reject`, { method: "POST" }))}
                  >
                    reject
                  </button>
                </>
              )}
              {po.status === "APPROVED" && (
                <>
                  <button className={btnCls} onClick={() => act(() => adminApi(`/admin/purchase-orders/${po.id}/receive`, { method: "POST" }))}>
                    📦 mark received
                  </button>
                  <button className={ghostBtnCls} onClick={() => act(() => adminApi(`/admin/purchase-orders/${po.id}/cancel`, { method: "POST" }))}>
                    cancel
                  </button>
                </>
              )}
            </div>
            {open[po.id] && (
              <div className="mt-3 border-t border-stone-700 pt-3">
                {po.rationale && <p className="mb-2 text-xs text-stone-400">🤖 {open[po.id].rationale}</p>}
                <table className="w-full text-left text-xs">
                  <thead className="text-stone-400">
                    <tr>
                      <th className="py-1">ingredient</th>
                      <th>qty</th>
                      <th>unit cost</th>
                      <th>why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {open[po.id].items.map((item) => (
                      <tr key={item.ingredient_id} className="border-t border-stone-700/60">
                        <td className="py-1">{item.ingredient_name}</td>
                        <td>
                          {po.status === "PENDING_APPROVAL" || po.status === "DRAFT" ? (
                            <QtyEditor
                              value={item.qty}
                              unit={item.unit}
                              onSave={(qty) =>
                                act(() =>
                                  adminApi(`/admin/purchase-orders/${po.id}/items/${item.ingredient_id}`, {
                                    method: "PATCH",
                                    body: { qty },
                                  }),
                                )
                              }
                            />
                          ) : (
                            `${item.qty} ${item.unit}`
                          )}
                        </td>
                        <td>{item.unit_cost ? `₹${item.unit_cost}` : "—"}</td>
                        <td className="text-stone-400">{item.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}
        {pos?.length === 0 && <p className="text-sm text-stone-400">No purchase orders yet — the agent drafts nightly at 02:30 IST.</p>}
      </div>

      <WastageSection />
    </div>
  );
}

function QtyEditor({ value, unit, onSave }: { value: string; unit: string; onSave: (qty: string) => void }) {
  const [qty, setQty] = useState(value);
  return (
    <span className="inline-flex items-center gap-1">
      <input className={`${inputCls} w-20`} value={qty} onChange={(e) => setQty(e.target.value)} />
      <span className="text-stone-400">{unit}</span>
      {qty !== value && (
        <button className={btnCls} onClick={() => onSave(qty)}>
          save
        </button>
      )}
    </span>
  );
}

function WastageSection() {
  const loadAll = useCallback(async () => {
    const [entries, ingredients] = await Promise.all([
      adminApi<Wastage[]>("/admin/wastage?limit=15"),
      adminApi<Ingredient[]>("/admin/ingredients"),
    ]);
    return { entries, ingredients };
  }, []);
  const { data, error, refresh, setError } = useLoad(loadAll);
  const [form, setForm] = useState({ ingredient_id: "", qty: "", reason: "SPOILAGE", note: "" });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await adminApi("/admin/wastage", {
        method: "POST",
        body: {
          ingredient_id: Number(form.ingredient_id),
          qty: form.qty,
          reason: form.reason,
          note: form.note || null,
        },
      });
      setForm({ ingredient_id: "", qty: "", reason: "SPOILAGE", note: "" });
      refresh();
    } catch (err) {
      setError(err instanceof AdminApiError ? err.message : "log failed");
    }
  };

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-amber-400">🗑 Wastage log</h2>
      <ErrorBar msg={error} />
      <form onSubmit={submit} className="mb-3 flex flex-wrap items-center gap-2">
        <select
          className={inputCls}
          required
          value={form.ingredient_id}
          onChange={(e) => setForm({ ...form, ingredient_id: e.target.value })}
        >
          <option value="">ingredient…</option>
          {(data?.ingredients ?? []).map((i) => (
            <option key={i.id} value={i.id}>
              {i.name} (stock {i.stock_qty} {i.unit})
            </option>
          ))}
        </select>
        <input className={`${inputCls} w-24`} placeholder="qty" required value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} />
        <select className={inputCls} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })}>
          {["SPOILAGE", "PREP_LOSS", "SPILLAGE", "EXPIRED", "OTHER"].map((r) => (
            <option key={r}>{r}</option>
          ))}
        </select>
        <input className={`${inputCls} w-56`} placeholder="note (optional)" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
        <button className={btnCls}>Log wastage</button>
      </form>
      <div className="space-y-1">
        {(data?.entries ?? []).map((w) => (
          <div key={w.id} className="flex flex-wrap items-center gap-3 rounded bg-stone-800 px-3 py-2 text-xs">
            <span className="text-stone-300">{new Date(w.at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}</span>
            <span className="font-semibold text-stone-100">
              {w.qty} {w.unit} {w.ingredient_name}
            </span>
            <span className="rounded bg-stone-700 px-2 py-0.5">{w.reason}</span>
            {w.note && <span className="text-stone-400">{w.note}</span>}
            <span className="ml-auto text-stone-400">stock → {w.stock_after}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
