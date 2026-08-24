"use client";

/** Inventory tab (Phase 6): agent-drafted purchase orders (approve/receive),
 *  on-demand agent runs, and the wastage log. */

import { useCallback, useState } from "react";
import {
  Badge,
  Btn,
  EmptyState,
  Eyebrow,
  Input,
  SectionHeading,
  Select,
  statusBadgeTone,
} from "../components/ui";
import { AdminApiError, adminApi } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

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
        <Select tone="dark" className="px-2 py-1" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all statuses</option>
          {["DRAFT", "PENDING_APPROVAL", "APPROVED", "RECEIVED", "REJECTED", "CANCELLED"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        <Btn variant="ghost" size="sm" onClick={refresh}>↻ refresh</Btn>
        <Btn variant="turmeric" size="sm" disabled={busy} onClick={draftNow}>
          {busy ? "🤖 drafting…" : "🤖 Run inventory agent now"}
        </Btn>
      </div>

      <div className="space-y-2">
        {(pos ?? []).map((po) => (
          <div key={po.id} className="rounded-lg bg-indigo-800 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <button className="font-mono text-turmeric-400 transition-colors duration-150 hover:text-turmeric-400" onClick={() => toggle(po.id)}>
                {open[po.id] ? "▾" : "▸"} PO #{po.id}
              </button>
              <Badge tone={statusBadgeTone(po.status)}>{po.status}</Badge>
              <span className="text-indigo-200">{po.supplier_name ?? "Unassigned supplier"}</span>
              {po.source === "AGENT" ? (
                <span className="ai-meta">🤖 {po.model ?? "agent"} · {po.prompt_version ?? ""} · {po.coverage_days}d cover</span>
              ) : (
                <span className="text-xs text-indigo-200/70">manual · {po.coverage_days}d cover</span>
              )}
              {po.expected_cost && <span className="tnum ml-auto font-display font-semibold">≈ ₹{Number(po.expected_cost).toFixed(0)}</span>}
              {po.status === "PENDING_APPROVAL" && (
                <>
                  <Btn variant="turmeric" size="sm" onClick={() => act(() => adminApi(`/admin/purchase-orders/${po.id}/approve`, { method: "POST" }))}>
                    ✅ approve
                  </Btn>
                  <Btn
                    variant="danger"
                    size="sm"
                    onClick={() => act(() => adminApi(`/admin/purchase-orders/${po.id}/reject`, { method: "POST" }))}
                  >
                    reject
                  </Btn>
                </>
              )}
              {po.status === "APPROVED" && (
                <>
                  <Btn variant="turmeric" size="sm" onClick={() => act(() => adminApi(`/admin/purchase-orders/${po.id}/receive`, { method: "POST" }))}>
                    📦 mark received
                  </Btn>
                  <Btn variant="danger" size="sm" onClick={() => act(() => adminApi(`/admin/purchase-orders/${po.id}/cancel`, { method: "POST" }))}>
                    cancel
                  </Btn>
                </>
              )}
            </div>
            {open[po.id] && (
              <div className="mt-3 border-t border-white/5 pt-3">
                {po.rationale && <p className="mb-2"><span className="ai-meta">🤖 {open[po.id].rationale}</span></p>}
                <table className="tnum w-full text-left text-xs">
                  <thead className="text-[11px] font-semibold uppercase tracking-[0.14em] text-turmeric-400/70">
                    <tr>
                      <th className="py-1">ingredient</th>
                      <th className="text-right">qty</th>
                      <th className="text-right">unit cost</th>
                      <th>why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {open[po.id].items.map((item) => (
                      <tr key={item.ingredient_id} className="border-t border-white/5">
                        <td className="py-1">{item.ingredient_name}</td>
                        <td className="text-right">
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
                        <td className="text-right">{item.unit_cost ? `₹${item.unit_cost}` : "—"}</td>
                        <td className="text-indigo-200/70">{item.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}
        {pos?.length === 0 && <EmptyState>No purchase orders yet — the agent drafts nightly at 02:30 IST.</EmptyState>}
      </div>

      <InvoiceSection onStockChanged={refresh} />
      <WastageSection />
    </div>
  );
}

type InvoiceLine = { name: string; qty: string; unit: string | null; unit_price: string | null; amount: string | null };
type InvoiceMatchLine = {
  po_ingredient_name: string;
  po_qty: string;
  invoice_name: string | null;
  invoice_qty: string | null;
  name_score: number;
  qty_ok: boolean;
};
type Invoice = {
  id: number;
  status: string;
  po_id: number | null;
  confidence: number;
  extraction: { supplier_name: string | null; invoice_number: string | null; invoice_date: string | null; lines: InvoiceLine[]; total: string | null } | null;
  match: { po_id: number; score: number; line_matches: InvoiceMatchLine[]; extra_invoice_lines: string[] } | null;
  model: string | null;
  created_at: string;
};

function InvoiceSection({ onStockChanged }: { onStockChanged: () => void }) {
  const loadInvoices = useCallback(() => adminApi<Invoice[]>("/admin/invoices"), []);
  const { data: invoices, error, refresh, setError } = useLoad(loadInvoices);
  const [busy, setBusy] = useState(false);

  const upload = async (file: File) => {
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setError("JPEG/PNG/WebP photos only");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const b64 = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve((reader.result as string).split(",")[1]);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      await adminApi("/admin/invoices", { method: "POST", body: { image_base64: b64, mime_type: file.type } });
      refresh();
    } catch (e) {
      setError(e instanceof AdminApiError ? e.message : "upload failed");
    } finally {
      setBusy(false);
    }
  };

  const decide = (id: number, action: "approve" | "reject") =>
    adminApi(`/admin/invoices/${id}/${action}`, { method: "POST", body: {} })
      .then(() => {
        refresh();
        if (action === "approve") onStockChanged();
      })
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "action failed"));

  return (
    <div>
      <div className="mb-2">
        <Eyebrow>Inventory</Eyebrow>
        <SectionHeading as="h3" kolam={false} className="text-base text-indigo-100">
          🧾 Supplier invoices (OCR)
        </SectionHeading>
      </div>
      <ErrorBar msg={error} />
      <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-indigo-600 px-2.5 py-1 text-xs font-semibold text-indigo-200 transition-colors duration-150 hover:border-turmeric-400 hover:text-turmeric-400">
        {busy ? "🔍 reading invoice…" : "📷 Upload invoice photo"}
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className="hidden"
          disabled={busy}
          onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
        />
      </label>
      <div className="mt-3 space-y-2">
        {(invoices ?? []).map((inv) => (
          <div key={inv.id} className="rounded-lg bg-indigo-800 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-turmeric-400">INV #{inv.id}</span>
              <Badge tone={statusBadgeTone(inv.status)}>{inv.status}</Badge>
              <span className="text-indigo-200">{inv.extraction?.supplier_name ?? "unknown supplier"}</span>
              <span className="ai-meta">🤖 {inv.model ?? "vision"}</span>
              <Badge tone={inv.confidence >= 0.8 ? "success" : "danger"}>
                confidence {(inv.confidence * 100).toFixed(0)}%
              </Badge>
              {inv.po_id && <span className="text-xs text-indigo-200/70">→ PO #{inv.po_id}</span>}
              {(inv.status === "MATCHED" || inv.status === "PENDING_REVIEW") && (
                <span className="ml-auto flex gap-2">
                  <Btn variant="turmeric" size="sm" disabled={!inv.po_id} onClick={() => decide(inv.id, "approve")}>
                    ✅ approve → stock in
                  </Btn>
                  <Btn variant="danger" size="sm" onClick={() => decide(inv.id, "reject")}>
                    reject
                  </Btn>
                </span>
              )}
            </div>
            {inv.match && (
              <div className="mt-2 text-xs text-indigo-200/70">
                {inv.match.line_matches.map((m, i) => (
                  <div key={i}>
                    {m.invoice_name
                      ? `${m.po_ingredient_name}: ordered ${m.po_qty} · billed ${m.invoice_qty} (${m.invoice_name}) ${m.qty_ok ? "✓" : "⚠ qty off"}`
                      : `${m.po_ingredient_name}: ⚠ missing from invoice`}
                  </div>
                ))}
                {inv.match.extra_invoice_lines.length > 0 && (
                  <div className="text-[#FF8B8B]">⚠ billed but not ordered: {inv.match.extra_invoice_lines.join(", ")}</div>
                )}
              </div>
            )}
          </div>
        ))}
        {invoices?.length === 0 && <EmptyState>No invoices yet — photograph a delivery challan to book stock in.</EmptyState>}
      </div>
    </div>
  );
}

function QtyEditor({ value, unit, onSave }: { value: string; unit: string; onSave: (qty: string) => void }) {
  const [qty, setQty] = useState(value);
  return (
    <span className="inline-flex items-center gap-1">
      <Input tone="dark" className="w-20 px-2 py-1 text-right" value={qty} onChange={(e) => setQty(e.target.value)} />
      <span className="text-indigo-200/70">{unit}</span>
      {qty !== value && (
        <Btn variant="turmeric" size="sm" onClick={() => onSave(qty)}>
          save
        </Btn>
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
      <div className="mb-2">
        <Eyebrow>Inventory</Eyebrow>
        <SectionHeading as="h3" kolam={false} className="text-base text-indigo-100">
          🗑 Wastage log
        </SectionHeading>
      </div>
      <ErrorBar msg={error} />
      <form onSubmit={submit} className="mb-3 flex flex-wrap items-center gap-2">
        <Select
          tone="dark"
          className="px-2 py-1"
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
        </Select>
        <Input tone="dark" className="w-24 px-2 py-1" placeholder="qty" required value={form.qty} onChange={(e) => setForm({ ...form, qty: e.target.value })} />
        <Select tone="dark" className="px-2 py-1" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })}>
          {["SPOILAGE", "PREP_LOSS", "SPILLAGE", "EXPIRED", "OTHER"].map((r) => (
            <option key={r}>{r}</option>
          ))}
        </Select>
        <Input tone="dark" className="w-56 px-2 py-1" placeholder="note (optional)" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
        <Btn variant="turmeric" size="sm">Log wastage</Btn>
      </form>
      <div className="space-y-1">
        {(data?.entries ?? []).map((w) => (
          <div key={w.id} className="flex flex-wrap items-center gap-3 rounded-lg bg-indigo-800 px-3 py-2 text-xs">
            <span className="text-indigo-200/70">{new Date(w.at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}</span>
            <span className="tnum font-semibold text-indigo-100">
              {w.qty} {w.unit} {w.ingredient_name}
            </span>
            <Badge tone="neutral">{w.reason}</Badge>
            {w.note && <span className="text-indigo-200/70">{w.note}</span>}
            <span className="tnum ml-auto text-indigo-200/70">stock → {w.stock_after}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
