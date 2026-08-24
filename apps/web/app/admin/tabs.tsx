"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Badge,
  Btn,
  ErrorBar as SharedErrorBar,
  EmptyState,
  Eyebrow,
  Input,
  Select,
  Textarea,
  statusBadgeTone,
  tableCls,
  tdCls,
  thCls,
  theadCls,
  trCls,
} from "../components/ui";
import {
  AdminApiError,
  AdminItem,
  AdminOrder,
  AuditRow,
  CacheStats,
  Combo,
  CostSummary,
  EvalRun,
  EvalRunDetail,
  Nutrition,
  SettingsRow,
  adminApi,
} from "./adminApi";

export function useLoad<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const refresh = useCallback(() => {
    load()
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "load failed"));
  }, [load]);
  useEffect(() => refresh(), [refresh]);
  return { data, error, refresh, setError };
}

/** Shared error bar with the admin surface's bottom margin baked in. */
export function ErrorBar({ msg }: { msg: string }) {
  if (!msg) return null;
  return (
    <div className="mb-3">
      <SharedErrorBar msg={`⚠ ${msg}`} />
    </div>
  );
}

/* -------------------------------------------------------------- Menu tab */

/** Read-only schedule summary — a weekday entry can be a single window (legacy)
 *  or a multi-window list (Phase 11). Compact text + full windows in the tooltip. */
const DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
function scheduleSummary(schedule: AdminItem["schedule"]): { text: string; title: string } {
  if (!schedule || Object.keys(schedule).length === 0)
    return { text: "always", title: "Served all day, every day" };
  const days = [
    ...DAY_ORDER.filter((d) => d in schedule),
    ...Object.keys(schedule).filter((d) => !DAY_ORDER.includes(d)),
  ];
  const windowsOf = (d: string) => {
    const entry = schedule[d];
    return Array.isArray(entry) ? entry : entry ? [entry] : [];
  };
  const total = days.reduce((n, d) => n + windowsOf(d).length, 0);
  const title = days
    .map(
      (d) =>
        `${d} ${windowsOf(d)
          .map((w) => `${w.start}–${w.end}`)
          .join(", ") || "—"}`,
    )
    .join(" · ");
  return {
    text: `${days.length}d · ${total} window${total === 1 ? "" : "s"}`,
    title,
  };
}

export function MenuTab() {
  const loadItems = useCallback(() => adminApi<AdminItem[]>("/admin/menu/items"), []);
  const { data: items, error, refresh, setError } = useLoad(loadItems);
  const [form, setForm] = useState({ name: "", category: "", price: "", is_veg: true, spice: 1 });
  const [priceEdits, setPriceEdits] = useState<Record<number, string>>({});

  const act = (fn: () => Promise<unknown>) =>
    fn()
      .then(refresh)
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "action failed"));

  return (
    <div>
      <ErrorBar msg={error} />
      <form
        className="mb-4 flex flex-wrap items-center gap-2 rounded-lg bg-indigo-800 p-3"
        onSubmit={(e) => {
          e.preventDefault();
          act(() =>
            adminApi("/admin/menu/items", {
              method: "POST",
              body: {
                name: form.name,
                category: form.category,
                price: form.price,
                is_veg: form.is_veg,
                spice_level: form.spice,
              },
            }),
          ).then(() => setForm({ name: "", category: "", price: "", is_veg: true, spice: 1 }));
        }}
      >
        <Eyebrow>New item</Eyebrow>
        <Input tone="dark" className="px-2 py-1" placeholder="Name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <Input tone="dark" className="px-2 py-1" placeholder="Category" required value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />
        <Input tone="dark" className="w-24 px-2 py-1" placeholder="₹ price" required value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} />
        <label className="flex items-center gap-1 text-xs text-indigo-200">
          <input type="checkbox" checked={form.is_veg} onChange={(e) => setForm({ ...form, is_veg: e.target.checked })} /> veg
        </label>
        <Select tone="dark" className="px-2 py-1" value={form.spice} onChange={(e) => setForm({ ...form, spice: Number(e.target.value) })}>
          {[0, 1, 2, 3].map((s) => (
            <option key={s} value={s}>{"🌶".repeat(s) || "no spice"}</option>
          ))}
        </Select>
        <Btn variant="gold" size="sm">Add</Btn>
      </form>

      <table className={tableCls}>
        <thead className={theadCls}>
          <tr><th className={thCls}>Item</th><th className={thCls}>Category</th><th className={`${thCls} text-right`}>Price ₹</th><th className={thCls}>Schedule</th><th className={`${thCls} text-right`}>86</th></tr>
        </thead>
        <tbody>
          {(items ?? []).map((i) => (
            <tr key={i.id} className={trCls}>
              <td className={tdCls}>
                {i.is_veg ? "🟢" : "🔴"} {i.name}
                {i.allergens.length > 0 && (
                  <span className="ml-2 text-xs text-turmeric-400">{i.allergens.join(", ")}</span>
                )}
              </td>
              <td className={`${tdCls} text-indigo-200`}>{i.category}</td>
              <td className={`${tdCls} text-right`}>
                <Input
                  tone="dark"
                  className="w-20 px-2 py-1 text-right"
                  value={priceEdits[i.id] ?? i.price}
                  onChange={(e) => setPriceEdits({ ...priceEdits, [i.id]: e.target.value })}
                  onBlur={() => {
                    const v = priceEdits[i.id];
                    if (v && v !== i.price)
                      act(() => adminApi(`/admin/menu/items/${i.id}`, { method: "PATCH", body: { price: v } }));
                  }}
                />
              </td>
              <td
                className={`${tdCls} text-xs text-indigo-200/70`}
                title={scheduleSummary(i.schedule).title}
              >
                {scheduleSummary(i.schedule).text}
              </td>
              <td className={`${tdCls} text-right`}>
                <Btn
                  variant={i.is_available ? "ghost" : "danger"}
                  size="sm"
                  onClick={() =>
                    act(() =>
                      adminApi(`/admin/menu/items/${i.id}/availability`, {
                        method: "POST",
                        body: { is_available: !i.is_available },
                      }),
                    )
                  }
                >
                  {i.is_available ? "86 it" : "86'd — restore"}
                </Btn>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------ Orders tab */

const NEXT: Record<string, string> = {
  PLACED: "CONFIRMED",
  CONFIRMED: "COOKING",
  COOKING: "READY",
  READY: "OUT_FOR_DELIVERY",
  OUT_FOR_DELIVERY: "DELIVERED",
};

export function OrdersTab() {
  const [status, setStatus] = useState("");
  const loadOrders = useCallback(
    () => adminApi<AdminOrder[]>(`/admin/orders${status ? `?status=${status}` : ""}`),
    [status],
  );
  const { data: orders, error, refresh, setError } = useLoad(loadOrders);

  const act = (fn: () => Promise<unknown>) =>
    fn()
      .then(refresh)
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "action failed"));

  return (
    <div>
      <ErrorBar msg={error} />
      <div className="mb-3 flex items-center gap-2">
        <Select tone="dark" className="px-2 py-1" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all statuses</option>
          {["PLACED", "CONFIRMED", "COOKING", "READY", "OUT_FOR_DELIVERY", "DELIVERED", "CANCELLED", "REFUNDED"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        <Btn variant="ghost" size="sm" onClick={refresh}>↻ refresh</Btn>
        <Btn
          variant="ghost"
          size="sm"
          title="Inject a signed mock Swiggy/Zomato order through the aggregator webhook path"
          onClick={() => act(() => adminApi("/admin/aggregator/simulate", { method: "POST", body: { count: 1 } }))}
        >
          🛵 Simulate aggregator order
        </Btn>
      </div>
      <div className="space-y-2">
        {(orders ?? []).map((o) => (
          <div key={o.id} className="flex flex-wrap items-center gap-3 rounded-lg bg-indigo-800 p-3 text-sm">
            <span className="font-mono text-turmeric-400">#{o.id}</span>
            <Badge tone={statusBadgeTone(o.status)}>{o.status}</Badge>
            <span className="text-indigo-200">{o.items.map((it) => `${it.qty}× ${it.name}`).join(", ")}</span>
            <span className="tnum ml-auto font-display font-semibold">₹{o.total}</span>
            {o.payment && <span className="text-xs text-indigo-200/70">pay: {o.payment.status}</span>}
            {NEXT[o.status] && (
              <Btn variant="gold" size="sm" onClick={() => act(() => adminApi(`/orders/${o.id}/status`, { method: "POST", body: { status: NEXT[o.status] } }))}>
                → {NEXT[o.status]}
              </Btn>
            )}
            {["PLACED", "CONFIRMED", "COOKING"].includes(o.status) && (
              <Btn
                variant="danger"
                size="sm"
                onClick={() => {
                  const reason = window.prompt("Cancel reason?");
                  if (reason) act(() => adminApi(`/admin/orders/${o.id}/cancel`, { method: "POST", body: { reason } }));
                }}
              >
                cancel
              </Btn>
            )}
            {["DELIVERED", "CANCELLED"].includes(o.status) && o.payment?.status === "CAPTURED" && (
              <Btn
                variant="danger"
                size="sm"
                onClick={() => {
                  const reason = window.prompt("Refund reason?");
                  if (reason) act(() => adminApi(`/admin/orders/${o.id}/refund`, { method: "POST", body: { reason } }));
                }}
              >
                refund
              </Btn>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ Combos tab */

export function CombosTab() {
  const loadCombos = useCallback(async () => {
    const [combos, items] = await Promise.all([
      adminApi<Combo[]>("/admin/combos"),
      adminApi<AdminItem[]>("/admin/menu/items"),
    ]);
    return { combos, items };
  }, []);
  const { data, error, refresh, setError } = useLoad(loadCombos);
  const [form, setForm] = useState<{ name: string; price: string; ids: number[] }>({ name: "", price: "", ids: [] });

  const act = (fn: () => Promise<unknown>) =>
    fn()
      .then(refresh)
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "action failed"));

  const itemName = (id: number) => data?.items.find((i) => i.id === id)?.name ?? `#${id}`;

  return (
    <div>
      <ErrorBar msg={error} />
      <form
        className="mb-4 rounded-lg bg-indigo-800 p-3"
        onSubmit={(e) => {
          e.preventDefault();
          act(() => adminApi("/admin/combos", { method: "POST", body: { name: form.name, price: form.price, item_ids: form.ids } })).then(() =>
            setForm({ name: "", price: "", ids: [] }),
          );
        }}
      >
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Eyebrow>New combo</Eyebrow>
          <Input tone="dark" className="px-2 py-1" placeholder="Name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <Input tone="dark" className="w-24 px-2 py-1" placeholder="₹ price" required value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} />
          <Btn variant="gold" size="sm" disabled={form.ids.length < 2}>Create draft ({form.ids.length} items)</Btn>
        </div>
        <div className="flex max-h-32 flex-wrap gap-2 overflow-y-auto">
          {(data?.items ?? []).map((i) => (
            <label key={i.id} className="flex items-center gap-1 rounded-full bg-indigo-700 px-2 py-0.5 text-xs text-indigo-100">
              <input
                type="checkbox"
                checked={form.ids.includes(i.id)}
                onChange={(e) =>
                  setForm({ ...form, ids: e.target.checked ? [...form.ids, i.id] : form.ids.filter((x) => x !== i.id) })
                }
              />
              {i.name}
            </label>
          ))}
        </div>
      </form>

      <div className="space-y-2">
        {(data?.combos ?? []).map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-3 rounded-lg bg-indigo-800 p-3 text-sm">
            <span className="font-semibold">{c.name}</span>
            <span className="text-indigo-200/70">{c.item_ids.map(itemName).join(" + ")}</span>
            <span className="tnum">₹{c.price}</span>
            <Badge tone={statusBadgeTone(c.status)}>{c.status}</Badge>
            {c.source === "AI_SUGGESTED" && <span className="ai-meta">🤖 AI suggested</span>}
            <span className="ml-auto" />
            {c.status !== "APPROVED" && (
              <Btn variant="gold" size="sm" onClick={() => act(() => adminApi(`/admin/combos/${c.id}/status`, { method: "POST", body: { status: "APPROVED" } }))}>approve</Btn>
            )}
            {c.status !== "REJECTED" && (
              <Btn variant="danger" size="sm" onClick={() => act(() => adminApi(`/admin/combos/${c.id}/status`, { method: "POST", body: { status: "REJECTED" } }))}>reject</Btn>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* --------------------------------------------------------- Nutrition tab */

export function NutritionTab() {
  const loadNutrition = useCallback(async () => {
    const [items, estimates] = await Promise.all([
      adminApi<AdminItem[]>("/admin/menu/items"),
      adminApi<Nutrition[]>("/admin/nutrition"),
    ]);
    return { items, byId: new Map(estimates.map((n) => [n.item_id, n])) };
  }, []);
  const { data, error, refresh, setError } = useLoad(loadNutrition);
  const [busy, setBusy] = useState<number | null>(null);

  const act = (fn: () => Promise<unknown>) =>
    fn()
      .then(refresh)
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "action failed"));

  return (
    <div>
      <ErrorBar msg={error} />
      <p className="mb-3 text-xs text-indigo-200/70">
        LLM drafts from the recipe mapping — nothing goes public without your approval.
      </p>
      <table className={tableCls}>
        <thead className={theadCls}>
          <tr><th className={thCls}>Item</th><th className={`${thCls} text-right`}>kcal</th><th className={`${thCls} text-right`}>P / C / F (g)</th><th className={`${thCls} text-right`}>conf.</th><th className={thCls}>Status</th><th className={`${thCls} text-right`}>Actions</th></tr>
        </thead>
        <tbody>
          {(data?.items ?? []).map((i) => {
            const n = data?.byId.get(i.id);
            return (
              <tr key={i.id} className={trCls}>
                <td className={tdCls}>{i.name}</td>
                <td className={`${tdCls} text-right`}>{n ? Math.round(n.estimate.calories_kcal) : "—"}</td>
                <td className={`${tdCls} text-right text-indigo-200`}>
                  {n ? `${n.estimate.protein_g} / ${n.estimate.carbs_g} / ${n.estimate.fat_g}` : "—"}
                </td>
                <td className={`${tdCls} text-right`}>{n ? `${Math.round(n.estimate.confidence * 100)}%` : ""}</td>
                <td className={tdCls}>
                  {n && <Badge tone={statusBadgeTone(n.status)}>{n.status}</Badge>}
                </td>
                <td className={`${tdCls} text-right`}>
                  <Btn
                    variant="ghost"
                    size="sm"
                    disabled={busy === i.id}
                    onClick={() => {
                      setBusy(i.id);
                      act(async () => {
                        const r = await adminApi<{ failed: { error: string }[] }>("/admin/nutrition/enrich", {
                          method: "POST",
                          body: { item_ids: [i.id] },
                        });
                        if (r.failed.length) throw new AdminApiError(422, r.failed[0].error);
                      }).finally(() => setBusy(null));
                    }}
                  >
                    {busy === i.id ? "…thinking" : n ? "re-enrich" : "✨ enrich"}
                  </Btn>
                  {n && n.status === "DRAFT" && (
                    <>
                      <Btn variant="gold" size="sm" className="ml-1" onClick={() => act(() => adminApi(`/admin/nutrition/${i.id}/status`, { method: "POST", body: { status: "APPROVED" } }))}>approve</Btn>
                      <Btn variant="danger" size="sm" className="ml-1" onClick={() => act(() => adminApi(`/admin/nutrition/${i.id}/status`, { method: "POST", body: { status: "REJECTED" } }))}>reject</Btn>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------------------------------------------------- Settings tab */

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

export function SettingsTab() {
  const loadSettings = useCallback(() => adminApi<SettingsRow>("/admin/settings"), []);
  const { data: settings, error, refresh, setError } = useLoad(loadSettings);
  const [pincodes, setPincodes] = useState<string | null>(null);
  const [hours, setHours] = useState<Record<string, { start: string; end: string }> | null>(null);

  const act = (fn: () => Promise<unknown>) =>
    fn()
      .then(() => {
        setPincodes(null);
        setHours(null);
        refresh();
      })
      .catch((e) => setError(e instanceof AdminApiError ? e.message : "action failed"));

  if (!settings) return <ErrorBar msg={error} />;
  const hrs = hours ?? settings.business_hours ?? {};

  return (
    <div className="max-w-2xl space-y-4">
      <ErrorBar msg={error} />
      <div className="rounded-lg bg-indigo-800 p-4">
        <Eyebrow className="mb-2 text-turmeric-400/80">Kitchen</Eyebrow>
        <Btn
          variant={settings.kitchen_paused ? "gold" : "danger"}
          size="sm"
          onClick={() => {
            const reason = settings.kitchen_paused ? null : window.prompt("Pause reason?");
            if (settings.kitchen_paused || reason)
              act(() => adminApi("/admin/settings/kitchen-pause", { method: "POST", body: { paused: !settings.kitchen_paused, reason } }));
          }}
        >
          {settings.kitchen_paused ? "▶ resume orders" : "⏸ pause kitchen"}
        </Btn>
        {settings.kitchen_paused && <span className="ml-3 text-sm text-[#FF8B8B]">orders are paused — checkout returns 503</span>}
      </div>

      <div className="rounded-lg bg-indigo-800 p-4">
        <Eyebrow className="mb-2 text-turmeric-400/80">Delivery pincodes</Eyebrow>
        <Textarea
          tone="dark"
          className="w-full"
          rows={2}
          value={pincodes ?? settings.delivery_pincodes.join(", ")}
          onChange={(e) => setPincodes(e.target.value)}
        />
        <Btn
          variant="gold"
          size="sm"
          className="mt-2"
          disabled={pincodes === null}
          onClick={() => act(() => adminApi("/admin/settings", { method: "PUT", body: { delivery_pincodes: (pincodes ?? "").split(",").map((p) => p.trim()).filter(Boolean) } }))}
        >
          Save pincodes
        </Btn>
      </div>

      <div className="rounded-lg bg-indigo-800 p-4">
        <Eyebrow className="mb-2 text-turmeric-400/80">
          Business hours <span className="normal-case tracking-normal text-indigo-200/60">(blank day = closed; none set = always open)</span>
        </Eyebrow>
        {DAYS.map((d) => (
          <div key={d} className="mb-1 flex items-center gap-2 text-sm">
            <span className="w-10 uppercase text-indigo-200/70">{d}</span>
            <Input tone="dark" className="w-20 px-2 py-1" placeholder="09:00" value={hrs[d]?.start ?? ""} onChange={(e) => setHours({ ...hrs, [d]: { start: e.target.value, end: hrs[d]?.end ?? "" } })} />
            <span className="text-indigo-300">–</span>
            <Input tone="dark" className="w-20 px-2 py-1" placeholder="22:00" value={hrs[d]?.end ?? ""} onChange={(e) => setHours({ ...hrs, [d]: { start: hrs[d]?.start ?? "", end: e.target.value } })} />
          </div>
        ))}
        <div className="mt-2 flex gap-2">
          <Btn
            variant="gold"
            size="sm"
            disabled={hours === null}
            onClick={() => {
              const filled = Object.fromEntries(Object.entries(hrs).filter(([, w]) => w.start && w.end));
              act(() => adminApi("/admin/settings", { method: "PUT", body: { business_hours: Object.keys(filled).length ? filled : null } }));
            }}
          >
            Save hours
          </Btn>
          <Btn variant="ghost" size="sm" onClick={() => act(() => adminApi("/admin/settings", { method: "PUT", body: { business_hours: null } }))}>
            Clear (always open)
          </Btn>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- Audit tab */

export function AuditTab() {
  const loadAudit = useCallback(() => adminApi<AuditRow[]>("/admin/audit?limit=100"), []);
  const { data: rows, error, refresh } = useLoad(loadAudit);
  const [filter, setFilter] = useState("");

  const visible = (rows ?? []).filter((r) => !filter || r.action.includes(filter) || r.entity.includes(filter));

  return (
    <div>
      <ErrorBar msg={error} />
      <div className="mb-3 flex gap-2">
        <Input tone="dark" className="px-2 py-1" placeholder="filter action/entity…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <Btn variant="ghost" size="sm" onClick={refresh}>↻</Btn>
      </div>
      <table className={tableCls}>
        <thead className={theadCls}>
          <tr><th className={thCls}>When (UTC)</th><th className={thCls}>Actor</th><th className={thCls}>Action</th><th className={thCls}>Entity</th><th className={thCls}>Detail</th></tr>
        </thead>
        <tbody>
          {visible.map((r) => (
            <tr key={r.id} className={`${trCls} align-top`}>
              <td className={`${tdCls} whitespace-nowrap text-indigo-200/70`}>{r.at.replace("T", " ").slice(0, 19)}</td>
              <td className={tdCls}>u{r.user_id}</td>
              <td className={`${tdCls} text-turmeric-400`}>{r.action}</td>
              <td className={tdCls}>{r.entity}</td>
              <td className={`${tdCls} max-w-md break-all font-mono text-xs text-indigo-200/70`}>{r.detail ? JSON.stringify(r.detail) : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------- Evals tab */

const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

function GateBadge({ passed }: { passed: boolean }) {
  return passed ? <Badge tone="success">PASS</Badge> : <Badge tone="danger">FAIL</Badge>;
}

export function EvalsTab() {
  const loadRuns = useCallback(() => adminApi<EvalRun[]>("/admin/eval-runs?limit=50"), []);
  const { data: runs, error, refresh } = useLoad(loadRuns);
  const [detail, setDetail] = useState<EvalRunDetail | null>(null);

  const openDetail = (id: number) =>
    adminApi<EvalRunDetail>(`/admin/eval-runs/${id}`).then(setDetail).catch(() => setDetail(null));

  const problemCases = (detail?.case_reports ?? []).filter(
    (c) => c.accuracy_problems.length || c.tool_violations.length || c.bypasses.length,
  );

  return (
    <div>
      <ErrorBar msg={error} />
      <div className="mb-3 flex items-center gap-3">
        <Eyebrow>
          Live eval scoreboard — merge gate: order_accuracy ≥ 95%, zero bypasses
        </Eyebrow>
        <Btn variant="ghost" size="sm" onClick={refresh}>↻</Btn>
      </div>
      <table className={tableCls}>
        <thead className={theadCls}>
          <tr>
            <th className={thCls}>Ran (UTC)</th><th className={thCls}>Commit</th><th className={thCls}>Trigger</th><th className={`${thCls} text-right`}>Cases</th>
            <th className={`${thCls} text-right`}>Order acc.</th><th className={`${thCls} text-right`}>Tool</th><th className={`${thCls} text-right`}>Bypasses</th><th className={`${thCls} text-right`}>Tone</th><th className={thCls}>Gates</th>
          </tr>
        </thead>
        <tbody>
          {(runs ?? []).map((r) => (
            <tr
              key={r.id}
              className={`${trCls} cursor-pointer`}
              onClick={() => openDetail(r.id)}
            >
              <td className={`${tdCls} whitespace-nowrap text-indigo-200/70`}>{r.ran_at.replace("T", " ").slice(0, 19)}</td>
              <td className={`${tdCls} font-mono text-indigo-200/70`}>{r.git_sha?.slice(0, 7) ?? "—"}</td>
              <td className={tdCls}>{r.trigger}</td>
              <td className={`${tdCls} tnum text-right`}>{r.cases}</td>
              <td className={`${tdCls} tnum text-right ${r.order_accuracy >= 0.95 ? "text-[#5BD69B]" : "text-[#FF8B8B]"}`}>{pct(r.order_accuracy)}</td>
              <td className={`${tdCls} tnum text-right ${r.tool_correctness >= 1 ? "text-[#5BD69B]" : "text-[#FF8B8B]"}`}>{pct(r.tool_correctness)}</td>
              <td className={`${tdCls} tnum text-right ${r.guardrail_bypasses === 0 ? "text-[#5BD69B]" : "text-[#FF8B8B]"}`}>
                {r.guardrail_bypasses}/{r.guardrail_cases}
              </td>
              <td className={`${tdCls} tnum text-right`}>{r.tone === null ? "—" : pct(r.tone)}</td>
              <td className={tdCls}><GateBadge passed={r.gates_passed} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      {runs && runs.length === 0 && (
        <EmptyState>No runs recorded yet — CI posts here after every live eval gate run.</EmptyState>
      )}
      {detail && (
        <div className="mt-4 rounded-lg bg-indigo-800 p-3 text-xs">
          <div className="mb-2 flex items-center gap-3">
            <span className="font-semibold text-indigo-100">
              Run #{detail.id} · <span className="font-mono">{detail.git_sha?.slice(0, 7) ?? "local"}</span> · <GateBadge passed={detail.gates_passed} />
            </span>
            <Btn variant="ghost" size="sm" onClick={() => setDetail(null)}>close</Btn>
          </div>
          {detail.failures.length > 0 && (
            <p className="mb-2 text-[#FF8B8B]">gate failures: {detail.failures.join("; ")}</p>
          )}
          {problemCases.length === 0 ? (
            <p className="text-[#5BD69B]">All {detail.cases} cases clean.</p>
          ) : (
            <ul className="space-y-1">
              {problemCases.map((c) => (
                <li key={c.id} className="border-t border-white/5 pt-1">
                  <span className="text-turmeric-400">{c.id}</span>{" "}
                  <span className="text-indigo-300">({c.language} · {c.tags.join(", ")})</span>
                  {c.bypasses.length > 0 && <span className="text-[#FF8B8B]"> BYPASS: {c.bypasses.join("; ")}</span>}
                  {c.tool_violations.length > 0 && <span className="text-[#FF8B8B]"> tool: {c.tool_violations.join("; ")}</span>}
                  {c.accuracy_problems.length > 0 && <span className="text-indigo-200"> {c.accuracy_problems.join("; ")}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- Costs tab */

const usd = (v: number) => `$${v.toFixed(4)}`;
const compact = (v: number) =>
  v >= 1_000_000 ? `${(v / 1_000_000).toFixed(1)}M` : v >= 1_000 ? `${(v / 1_000).toFixed(1)}k` : `${v}`;

export function CostsTab() {
  const loadCosts = useCallback(() => adminApi<CostSummary>("/admin/costs/daily?days=14"), []);
  const { data: summary, error, refresh } = useLoad(loadCosts);
  const loadCache = useCallback(() => adminApi<CacheStats>("/admin/costs/cache"), []);
  const { data: cache, refresh: refreshCache } = useLoad(loadCache);

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

  const cachePanel = cache && (
    <div className="mb-5 rounded-lg bg-indigo-800 p-3 text-xs">
      <div className="mb-2 flex items-center gap-2">
        <Eyebrow>Cache efficiency (Phase 9)</Eyebrow>
        <Btn variant="ghost" size="sm" onClick={refreshCache}>↻</Btn>
      </div>
      <div className="flex flex-wrap gap-8">
        <div>
          <p className="text-indigo-200/70">
            Semantic cache {cache.semcache_enabled ? `(cosine ≥ ${cache.semcache_threshold})` : "(disabled)"}
          </p>
          <p className="tnum font-display text-3xl font-semibold text-turmeric-400">{pct(cache.semcache.hit_rate)} <span className="text-base">hit rate</span></p>
          <p className="text-indigo-200/70">
            {cache.semcache.exact_hits} exact · {cache.semcache.semantic_hits} semantic ·{" "}
            {cache.semcache.misses} misses · {cache.semcache.stores} stores · {cache.semcache.flushes} flushes
          </p>
        </div>
        <div>
          <p className="text-indigo-200/70">Provider prompt cache (prefix-stable layout)</p>
          <p className="tnum font-display text-3xl font-semibold text-turmeric-400">{pct(cache.prompt_cache.cached_share)} <span className="text-base">of prompt tokens cached</span></p>
          <p className="text-indigo-200/70">
            {compact(cache.prompt_cache.cached_prompt_tokens)} / {compact(cache.prompt_cache.prompt_tokens)} prompt tok
            over {cache.prompt_cache.calls} calls
          </p>
        </div>
      </div>
      <p className="mt-2 text-indigo-300">
        Running counters on the cache Redis (LRU may reset them) — billing truth stays in Langfuse.
      </p>
    </div>
  );

  if (summary && !summary.configured) {
    return (
      <div>
        {cachePanel}
        <p className="rounded-lg bg-indigo-800 p-4 text-sm text-indigo-200">
          Langfuse keys are not configured on the AI service — cost tracking is off.
          Set LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY to enable the dashboard.
        </p>
      </div>
    );
  }

  const days = summary?.days ?? [];
  const last7 = days.slice(0, 7).reduce((acc, d) => acc + d.cost_usd, 0);

  return (
    <div>
      <ErrorBar msg={error} />
      {cachePanel}
      <div className="mb-4 flex items-center gap-6">
        <div>
          <Eyebrow>Last 7 days</Eyebrow>
          <p className="tnum font-display text-3xl font-semibold text-turmeric-400">{usd(last7)}</p>
        </div>
        <div>
          <Eyebrow>Last 14 days</Eyebrow>
          <p className="tnum font-display text-3xl font-semibold text-indigo-100">{usd(summary?.total_cost_usd ?? 0)}</p>
        </div>
        <Btn variant="ghost" size="sm" onClick={refresh}>↻</Btn>
      </div>
      <table className={tableCls}>
        <thead className={theadCls}>
          <tr>
            <th className={thCls}>Date</th><th className={`${thCls} text-right`}>Traces</th><th className={`${thCls} text-right`}>LLM calls</th><th className={`${thCls} text-right`}>Cost</th><th className={thCls}>Per-model breakdown</th>
          </tr>
        </thead>
        <tbody>
          {days.map((d) => (
            <tr key={d.date} className={`${trCls} align-top`}>
              <td className={`${tdCls} whitespace-nowrap text-indigo-200/70`}>{d.date}</td>
              <td className={`${tdCls} tnum text-right`}>{d.traces}</td>
              <td className={`${tdCls} tnum text-right`}>{d.observations}</td>
              <td className={`${tdCls} tnum text-right text-turmeric-400`}>{usd(d.cost_usd)}</td>
              <td className={`${tdCls} text-indigo-200/70`}>
                {d.models.length === 0
                  ? "—"
                  : d.models.map((m) => (
                      <span key={m.model} className="mr-3 inline-block">
                        <span className="text-indigo-200">{m.model}</span> {usd(m.cost_usd)}{" "}
                        ({compact(m.input_tokens)}→{compact(m.output_tokens)} tok, {m.calls} calls)
                      </span>
                    ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {days.length === 0 && (
        <EmptyState>No cost data yet — traces appear after the first LLM calls.</EmptyState>
      )}
    </div>
  );
}
