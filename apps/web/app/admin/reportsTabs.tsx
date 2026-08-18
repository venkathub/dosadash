"use client";

/** Reports + CRM tabs (Phase 5): sales rollups, dish P&L, GST CSV export,
 *  forecast-vs-actual chart with anomaly flags, RFM/churn segments. */

import { useCallback, useState } from "react";
import {
  CrmReport,
  DishPnlReport,
  ForecastReport,
  SalesReport,
  adminApi,
  adminApiText,
} from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

const btnCls =
  "rounded bg-amber-500 px-2 py-1 text-xs font-semibold text-stone-900 hover:bg-amber-400 disabled:opacity-40";
const ghostBtnCls =
  "rounded border border-stone-600 px-2 py-1 text-xs text-stone-300 hover:border-amber-400 hover:text-amber-300";

const inr = (v: number) => `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

/* ------------------------------------------------------------- Reports tab */

export function ReportsTab() {
  const [granularity, setGranularity] = useState<"daily" | "weekly" | "monthly">("daily");
  const windowDays = granularity === "daily" ? 30 : granularity === "weekly" ? 84 : 365;

  const loadSales = useCallback(
    () => adminApi<SalesReport>(`/admin/reports/sales?granularity=${granularity}&days=${windowDays}`),
    [granularity, windowDays],
  );
  const loadPnl = useCallback(() => adminApi<DishPnlReport>("/admin/reports/dish-pnl?days=30"), []);
  const loadForecast = useCallback(
    () => adminApi<ForecastReport>("/admin/reports/forecast-vs-actual?days=14"),
    [],
  );
  const sales = useLoad(loadSales);
  const pnl = useLoad(loadPnl);
  const forecast = useLoad(loadForecast);
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [csvError, setCsvError] = useState("");

  const downloadGst = async () => {
    try {
      const csv = await adminApiText(`/admin/reports/gst.csv?month=${month}`);
      const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `gst-${month}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      setCsvError("");
    } catch {
      setCsvError("GST export failed");
    }
  };

  return (
    <div className="space-y-8">
      <section>
        <div className="mb-2 flex items-center gap-2">
          <h2 className="text-sm font-semibold uppercase text-stone-400">Sales</h2>
          {(["daily", "weekly", "monthly"] as const).map((g) => (
            <button
              key={g}
              onClick={() => setGranularity(g)}
              className={granularity === g ? btnCls : ghostBtnCls}
            >
              {g}
            </button>
          ))}
        </div>
        <ErrorBar msg={sales.error} />
        {sales.data && (
          <>
            <p className="mb-2 text-sm text-stone-300">
              <span className="text-amber-300">{inr(sales.data.total_revenue)}</span> revenue ·{" "}
              {sales.data.total_orders} orders · {inr(sales.data.total_gst)} GST (last{" "}
              {sales.data.days}d)
            </p>
            <table className="w-full text-left text-xs">
              <thead className="uppercase text-stone-400">
                <tr><th className="p-2">Period</th><th>Orders</th><th>Revenue</th><th>GST</th><th>AOV</th></tr>
              </thead>
              <tbody>
                {sales.data.buckets.slice(-12).reverse().map((b) => (
                  <tr key={b.period} className="border-t border-stone-800">
                    <td className="p-2 text-stone-400">{b.period}</td>
                    <td>{b.orders}</td>
                    <td className="text-amber-300">{inr(b.revenue)}</td>
                    <td>{inr(b.gst)}</td>
                    <td>{inr(b.aov)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section>
        <div className="mb-2 flex items-center gap-3">
          <h2 className="text-sm font-semibold uppercase text-stone-400">
            Forecast vs actual (dishes/day)
          </h2>
          {forecast.data?.model_version && (
            <span className="text-xs text-stone-500">model {forecast.data.model_version}</span>
          )}
        </div>
        <ErrorBar msg={forecast.error} />
        {forecast.data && forecast.data.points.length === 0 && (
          <p className="text-sm text-stone-400">
            No forecasts yet — the nightly scoring job (02:00 IST) hasn&apos;t run.
          </p>
        )}
        {forecast.data && forecast.data.points.length > 0 && (
          <ForecastChart report={forecast.data} />
        )}
        {forecast.data && forecast.data.dish_anomalies.length > 0 && (
          <div className="mt-3">
            <h3 className="mb-1 text-xs font-semibold uppercase text-red-300">Anomalies</h3>
            {forecast.data.dish_anomalies.map((a) => (
              <p key={`${a.item_id}-${a.date}`} className="text-xs text-stone-300">
                <span className="text-red-300">⚑</span> {a.date} — {a.name}: forecast{" "}
                {a.forecast_qty}, actual {a.actual_qty} ({a.deviation_pct}% off)
              </p>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase text-stone-400">
          Dish P&L (last 30d)
        </h2>
        <ErrorBar msg={pnl.error} />
        {pnl.data && (
          <table className="w-full text-left text-xs">
            <thead className="uppercase text-stone-400">
              <tr>
                <th className="p-2">Dish</th><th>Qty</th><th>Revenue</th>
                <th>Ingredient cost</th><th>Margin</th><th>Margin %</th>
              </tr>
            </thead>
            <tbody>
              {pnl.data.rows.slice(0, 20).map((r) => (
                <tr key={r.item_id} className="border-t border-stone-800">
                  <td className="p-2">
                    {r.name} <span className="text-stone-500">{r.category}</span>
                  </td>
                  <td>{r.qty}</td>
                  <td className="text-amber-300">{inr(r.revenue)}</td>
                  <td>
                    {inr(r.ingredient_cost)}{" "}
                    {r.cost_source === "estimated" && (
                      <span title="No priced recipe — 35% food-cost estimate" className="text-stone-500">
                        est.
                      </span>
                    )}
                  </td>
                  <td className={r.margin >= 0 ? "text-green-300" : "text-red-300"}>{inr(r.margin)}</td>
                  <td>{r.margin_pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase text-stone-400">GST export</h2>
        <ErrorBar msg={csvError} />
        <div className="flex items-center gap-2">
          <input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="rounded bg-stone-700 px-2 py-1 text-sm text-stone-100"
          />
          <button className={btnCls} onClick={downloadGst}>Download CSV</button>
        </div>
      </section>
    </div>
  );
}

/** Dependency-free SVG chart: actual bars, forecast line, red anomaly dots. */
function ForecastChart({ report }: { report: ForecastReport }) {
  const points = report.points;
  const W = 640, H = 160, pad = 24;
  const max = Math.max(1, ...points.map((p) => Math.max(p.forecast_qty ?? 0, p.actual_qty ?? 0)));
  const x = (i: number) => pad + (i * (W - 2 * pad)) / Math.max(1, points.length - 1);
  const y = (v: number) => H - pad - (v / max) * (H - 2 * pad);
  const line = points
    .map((p, i) => (p.forecast_qty === null ? null : `${x(i)},${y(p.forecast_qty)}`))
    .filter(Boolean)
    .join(" ");
  const barW = Math.max(3, (W - 2 * pad) / points.length - 6);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-3xl rounded bg-stone-800/60">
      {points.map((p, i) =>
        p.actual_qty === null ? null : (
          <rect
            key={p.date}
            x={x(i) - barW / 2}
            y={y(p.actual_qty)}
            width={barW}
            height={H - pad - y(p.actual_qty)}
            className="fill-stone-500"
          />
        ),
      )}
      <polyline points={line} fill="none" strokeWidth={2} className="stroke-amber-400" />
      {points.map((p, i) =>
        p.anomaly && p.actual_qty !== null ? (
          <circle key={`a-${p.date}`} cx={x(i)} cy={y(p.actual_qty)} r={4} className="fill-red-400">
            <title>{`${p.date}: forecast ${p.forecast_qty}, actual ${p.actual_qty}`}</title>
          </circle>
        ) : null,
      )}
      <text x={pad} y={12} className="fill-stone-400 text-[10px]">
        ▬ actual · ─ forecast · ● anomaly (max {Math.round(max)}/day)
      </text>
    </svg>
  );
}

/* ----------------------------------------------------------------- CRM tab */

const TIER_ORDER = ["CHAMPION", "LOYAL", "POTENTIAL", "NEW", "REGULAR", "AT_RISK", "LOST"];

export function CrmTab() {
  const loadCrm = useCallback(() => adminApi<CrmReport>("/admin/crm/segments"), []);
  const { data, error, refresh } = useLoad(loadCrm);

  if (data && data.computed_at === null) {
    return (
      <p className="rounded bg-stone-800/60 p-4 text-sm text-stone-300">
        No segments yet — the nightly CRM scoring job (03:00 IST) hasn&apos;t run.
      </p>
    );
  }
  const tiers = (data?.tiers ?? [])
    .slice()
    .sort((a, b) => TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier));
  return (
    <div>
      <ErrorBar msg={error} />
      <div className="mb-2 flex items-center gap-3">
        <h2 className="text-sm font-semibold uppercase text-stone-400">Segments</h2>
        {data?.computed_at && (
          <span className="text-xs text-stone-500">
            scored {new Date(data.computed_at).toLocaleString()}
          </span>
        )}
        <button className={ghostBtnCls} onClick={refresh}>↻</button>
      </div>
      <div className="mb-6 flex flex-wrap gap-3">
        {tiers.map((t) => (
          <div key={t.tier} className="rounded bg-stone-800/60 px-4 py-3">
            <p className="text-xs uppercase text-stone-400">{t.tier}</p>
            <p className="text-lg font-semibold text-amber-300">{t.users}</p>
            <p className="text-xs text-stone-400">
              LTV {inr(t.total_ltv)} · churn {(t.avg_churn_risk * 100).toFixed(0)}%
            </p>
          </div>
        ))}
      </div>
      <h2 className="mb-2 text-sm font-semibold uppercase text-stone-400">
        Win-back targets <span className="text-stone-500">(high LTV × churn risk)</span>
      </h2>
      <table className="w-full text-left text-xs">
        <thead className="uppercase text-stone-400">
          <tr><th className="p-2">Customer</th><th>Tier</th><th>Churn risk</th><th>LTV</th></tr>
        </thead>
        <tbody>
          {(data?.at_risk ?? []).map((u) => (
            <tr key={u.user_id} className="border-t border-stone-800">
              <td className="p-2">
                {u.name ?? "—"} <span className="text-stone-500">{u.phone}</span>
              </td>
              <td>{u.rfm_tier}</td>
              <td className={u.churn_risk >= 0.8 ? "text-red-300" : "text-amber-300"}>
                {(u.churn_risk * 100).toFixed(0)}%
              </td>
              <td>{inr(u.ltv)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
