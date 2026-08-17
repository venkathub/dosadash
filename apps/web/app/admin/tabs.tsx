"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AdminApiError,
  AdminItem,
  AdminOrder,
  AuditRow,
  Combo,
  Nutrition,
  SettingsRow,
  adminApi,
} from "./adminApi";

const inputCls =
  "rounded bg-stone-700 px-2 py-1 text-sm text-stone-100 placeholder-stone-400 outline-none focus:ring-1 focus:ring-amber-400";
const btnCls =
  "rounded bg-amber-500 px-2 py-1 text-xs font-semibold text-stone-900 hover:bg-amber-400 disabled:opacity-40";
const ghostBtnCls =
  "rounded border border-stone-600 px-2 py-1 text-xs text-stone-300 hover:border-amber-400 hover:text-amber-300";

function useLoad<T>(load: () => Promise<T>) {
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

function ErrorBar({ msg }: { msg: string }) {
  if (!msg) return null;
  return <p className="mb-3 rounded bg-red-900/60 px-3 py-2 text-sm text-red-200">⚠ {msg}</p>;
}

/* -------------------------------------------------------------- Menu tab */

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
        className="mb-4 flex flex-wrap items-center gap-2 rounded bg-stone-800 p-3"
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
        <span className="text-xs uppercase tracking-wide text-stone-400">New item</span>
        <input className={inputCls} placeholder="Name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input className={inputCls} placeholder="Category" required value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} />
        <input className={`${inputCls} w-24`} placeholder="₹ price" required value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} />
        <label className="flex items-center gap-1 text-xs text-stone-300">
          <input type="checkbox" checked={form.is_veg} onChange={(e) => setForm({ ...form, is_veg: e.target.checked })} /> veg
        </label>
        <select className={inputCls} value={form.spice} onChange={(e) => setForm({ ...form, spice: Number(e.target.value) })}>
          {[0, 1, 2, 3].map((s) => (
            <option key={s} value={s}>{"🌶".repeat(s) || "no spice"}</option>
          ))}
        </select>
        <button className={btnCls}>Add</button>
      </form>

      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase text-stone-400">
          <tr><th className="p-2">Item</th><th>Category</th><th>Price ₹</th><th>Schedule</th><th className="text-right">86</th></tr>
        </thead>
        <tbody>
          {(items ?? []).map((i) => (
            <tr key={i.id} className="border-t border-stone-800">
              <td className="p-2">
                {i.is_veg ? "🟢" : "🔴"} {i.name}
                {i.allergens.length > 0 && (
                  <span className="ml-2 text-xs text-orange-300">{i.allergens.join(", ")}</span>
                )}
              </td>
              <td className="text-stone-400">{i.category}</td>
              <td>
                <input
                  className={`${inputCls} w-20`}
                  value={priceEdits[i.id] ?? i.price}
                  onChange={(e) => setPriceEdits({ ...priceEdits, [i.id]: e.target.value })}
                  onBlur={() => {
                    const v = priceEdits[i.id];
                    if (v && v !== i.price)
                      act(() => adminApi(`/admin/menu/items/${i.id}`, { method: "PATCH", body: { price: v } }));
                  }}
                />
              </td>
              <td className="text-xs text-stone-400">{i.schedule ? Object.keys(i.schedule).join(" ") : "always"}</td>
              <td className="p-2 text-right">
                <button
                  className={i.is_available ? ghostBtnCls : `${btnCls} bg-red-500 hover:bg-red-400`}
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
                </button>
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
        <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all statuses</option>
          {["PLACED", "CONFIRMED", "COOKING", "READY", "OUT_FOR_DELIVERY", "DELIVERED", "CANCELLED", "REFUNDED"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <button className={ghostBtnCls} onClick={refresh}>↻ refresh</button>
      </div>
      <div className="space-y-2">
        {(orders ?? []).map((o) => (
          <div key={o.id} className="flex flex-wrap items-center gap-3 rounded bg-stone-800 p-3 text-sm">
            <span className="font-mono text-amber-300">#{o.id}</span>
            <span className="rounded bg-stone-700 px-2 py-0.5 text-xs">{o.status}</span>
            <span className="text-stone-300">{o.items.map((it) => `${it.qty}× ${it.name}`).join(", ")}</span>
            <span className="ml-auto font-semibold">₹{o.total}</span>
            {o.payment && <span className="text-xs text-stone-400">pay: {o.payment.status}</span>}
            {NEXT[o.status] && (
              <button className={btnCls} onClick={() => act(() => adminApi(`/orders/${o.id}/status`, { method: "POST", body: { status: NEXT[o.status] } }))}>
                → {NEXT[o.status]}
              </button>
            )}
            {["PLACED", "CONFIRMED", "COOKING"].includes(o.status) && (
              <button
                className={ghostBtnCls}
                onClick={() => {
                  const reason = window.prompt("Cancel reason?");
                  if (reason) act(() => adminApi(`/admin/orders/${o.id}/cancel`, { method: "POST", body: { reason } }));
                }}
              >
                cancel
              </button>
            )}
            {["DELIVERED", "CANCELLED"].includes(o.status) && o.payment?.status === "CAPTURED" && (
              <button
                className={`${ghostBtnCls} border-red-500 text-red-300`}
                onClick={() => {
                  const reason = window.prompt("Refund reason?");
                  if (reason) act(() => adminApi(`/admin/orders/${o.id}/refund`, { method: "POST", body: { reason } }));
                }}
              >
                refund
              </button>
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
        className="mb-4 rounded bg-stone-800 p-3"
        onSubmit={(e) => {
          e.preventDefault();
          act(() => adminApi("/admin/combos", { method: "POST", body: { name: form.name, price: form.price, item_ids: form.ids } })).then(() =>
            setForm({ name: "", price: "", ids: [] }),
          );
        }}
      >
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-stone-400">New combo</span>
          <input className={inputCls} placeholder="Name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input className={`${inputCls} w-24`} placeholder="₹ price" required value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} />
          <button className={btnCls} disabled={form.ids.length < 2}>Create draft ({form.ids.length} items)</button>
        </div>
        <div className="flex max-h-32 flex-wrap gap-2 overflow-y-auto">
          {(data?.items ?? []).map((i) => (
            <label key={i.id} className="flex items-center gap-1 rounded bg-stone-700 px-2 py-0.5 text-xs">
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
          <div key={c.id} className="flex flex-wrap items-center gap-3 rounded bg-stone-800 p-3 text-sm">
            <span className="font-semibold">{c.name}</span>
            <span className="text-stone-400">{c.item_ids.map(itemName).join(" + ")}</span>
            <span>₹{c.price}</span>
            <span className={`rounded px-2 py-0.5 text-xs ${c.status === "APPROVED" ? "bg-green-800 text-green-200" : c.status === "REJECTED" ? "bg-red-900 text-red-300" : "bg-stone-700"}`}>
              {c.status}{c.source === "AI_SUGGESTED" ? " · 🤖 AI" : ""}
            </span>
            <span className="ml-auto" />
            {c.status !== "APPROVED" && (
              <button className={btnCls} onClick={() => act(() => adminApi(`/admin/combos/${c.id}/status`, { method: "POST", body: { status: "APPROVED" } }))}>approve</button>
            )}
            {c.status !== "REJECTED" && (
              <button className={ghostBtnCls} onClick={() => act(() => adminApi(`/admin/combos/${c.id}/status`, { method: "POST", body: { status: "REJECTED" } }))}>reject</button>
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
      <p className="mb-3 text-xs text-stone-400">
        LLM drafts from the recipe mapping — nothing goes public without your approval.
      </p>
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase text-stone-400">
          <tr><th className="p-2">Item</th><th>kcal</th><th>P / C / F (g)</th><th>conf.</th><th>Status</th><th className="text-right">Actions</th></tr>
        </thead>
        <tbody>
          {(data?.items ?? []).map((i) => {
            const n = data?.byId.get(i.id);
            return (
              <tr key={i.id} className="border-t border-stone-800">
                <td className="p-2">{i.name}</td>
                <td>{n ? Math.round(n.estimate.calories_kcal) : "—"}</td>
                <td className="text-stone-400">
                  {n ? `${n.estimate.protein_g} / ${n.estimate.carbs_g} / ${n.estimate.fat_g}` : "—"}
                </td>
                <td>{n ? `${Math.round(n.estimate.confidence * 100)}%` : ""}</td>
                <td>
                  {n && (
                    <span className={`rounded px-2 py-0.5 text-xs ${n.status === "APPROVED" ? "bg-green-800 text-green-200" : n.status === "REJECTED" ? "bg-red-900 text-red-300" : "bg-amber-900 text-amber-200"}`}>
                      {n.status}
                    </span>
                  )}
                </td>
                <td className="p-2 text-right">
                  <button
                    className={ghostBtnCls}
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
                  </button>
                  {n && n.status === "DRAFT" && (
                    <>
                      <button className={`${btnCls} ml-1`} onClick={() => act(() => adminApi(`/admin/nutrition/${i.id}/status`, { method: "POST", body: { status: "APPROVED" } }))}>approve</button>
                      <button className={`${ghostBtnCls} ml-1`} onClick={() => act(() => adminApi(`/admin/nutrition/${i.id}/status`, { method: "POST", body: { status: "REJECTED" } }))}>reject</button>
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
      <div className="rounded bg-stone-800 p-4">
        <h3 className="mb-2 text-sm font-semibold">Kitchen</h3>
        <button
          className={settings.kitchen_paused ? btnCls : `${ghostBtnCls} border-red-500 text-red-300`}
          onClick={() => {
            const reason = settings.kitchen_paused ? null : window.prompt("Pause reason?");
            if (settings.kitchen_paused || reason)
              act(() => adminApi("/admin/settings/kitchen-pause", { method: "POST", body: { paused: !settings.kitchen_paused, reason } }));
          }}
        >
          {settings.kitchen_paused ? "▶ resume orders" : "⏸ pause kitchen"}
        </button>
        {settings.kitchen_paused && <span className="ml-3 text-sm text-red-300">orders are paused — checkout returns 503</span>}
      </div>

      <div className="rounded bg-stone-800 p-4">
        <h3 className="mb-2 text-sm font-semibold">Delivery pincodes</h3>
        <textarea
          className={`${inputCls} w-full`}
          rows={2}
          value={pincodes ?? settings.delivery_pincodes.join(", ")}
          onChange={(e) => setPincodes(e.target.value)}
        />
        <button
          className={`${btnCls} mt-2`}
          disabled={pincodes === null}
          onClick={() => act(() => adminApi("/admin/settings", { method: "PUT", body: { delivery_pincodes: (pincodes ?? "").split(",").map((p) => p.trim()).filter(Boolean) } }))}
        >
          Save pincodes
        </button>
      </div>

      <div className="rounded bg-stone-800 p-4">
        <h3 className="mb-2 text-sm font-semibold">Business hours <span className="font-normal text-stone-400">(blank day = closed; none set = always open)</span></h3>
        {DAYS.map((d) => (
          <div key={d} className="mb-1 flex items-center gap-2 text-sm">
            <span className="w-10 uppercase text-stone-400">{d}</span>
            <input className={`${inputCls} w-20`} placeholder="09:00" value={hrs[d]?.start ?? ""} onChange={(e) => setHours({ ...hrs, [d]: { start: e.target.value, end: hrs[d]?.end ?? "" } })} />
            <span className="text-stone-500">–</span>
            <input className={`${inputCls} w-20`} placeholder="22:00" value={hrs[d]?.end ?? ""} onChange={(e) => setHours({ ...hrs, [d]: { start: hrs[d]?.start ?? "", end: e.target.value } })} />
          </div>
        ))}
        <div className="mt-2 flex gap-2">
          <button
            className={btnCls}
            disabled={hours === null}
            onClick={() => {
              const filled = Object.fromEntries(Object.entries(hrs).filter(([, w]) => w.start && w.end));
              act(() => adminApi("/admin/settings", { method: "PUT", body: { business_hours: Object.keys(filled).length ? filled : null } }));
            }}
          >
            Save hours
          </button>
          <button className={ghostBtnCls} onClick={() => act(() => adminApi("/admin/settings", { method: "PUT", body: { business_hours: null } }))}>
            Clear (always open)
          </button>
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
        <input className={inputCls} placeholder="filter action/entity…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <button className={ghostBtnCls} onClick={refresh}>↻</button>
      </div>
      <table className="w-full text-left text-xs">
        <thead className="uppercase text-stone-400">
          <tr><th className="p-2">When (UTC)</th><th>Actor</th><th>Action</th><th>Entity</th><th>Detail</th></tr>
        </thead>
        <tbody>
          {visible.map((r) => (
            <tr key={r.id} className="border-t border-stone-800 align-top">
              <td className="p-2 whitespace-nowrap text-stone-400">{r.at.replace("T", " ").slice(0, 19)}</td>
              <td>u{r.user_id}</td>
              <td className="text-amber-300">{r.action}</td>
              <td>{r.entity}</td>
              <td className="max-w-md break-all text-stone-400">{r.detail ? JSON.stringify(r.detail) : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
