"use client";

/** Reports + CRM tabs (Phase 5): sales rollups, dish P&L, GST CSV export,
 *  forecast-vs-actual chart with anomaly flags, RFM/churn segments. */

import { useCallback, useState } from "react";
import {
  Btn,
  EmptyState,
  Eyebrow,
  Input,
  SectionHeading,
  tableCls,
  tdCls,
  thCls,
  theadCls,
  trCls,
} from "../components/ui";
import {
  CrmReport,
  DishPnlReport,
  ForecastReport,
  SalesReport,
  adminApi,
  adminApiText,
} from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

const inr = (v: number) => `₹${v.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

function ReportHeading({ children }: { children: React.ReactNode }) {
  return (
    <SectionHeading as="h3" kolam={false} className="text-base text-indigo-100">
      {children}
    </SectionHeading>
  );
}

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
          <ReportHeading>Sales</ReportHeading>
          {(["daily", "weekly", "monthly"] as const).map((g) => (
            <Btn
              key={g}
              onClick={() => setGranularity(g)}
              variant={granularity === g ? "gold" : "ghost"}
              size="sm"
            >
              {g}
            </Btn>
          ))}
        </div>
        <ErrorBar msg={sales.error} />
        {sales.data && (
          <>
            <p className="tnum mb-2 text-sm text-indigo-200">
              <span className="font-display text-turmeric-400">{inr(sales.data.total_revenue)}</span> revenue ·{" "}
              {sales.data.total_orders} orders · {inr(sales.data.total_gst)} GST (last{" "}
              {sales.data.days}d)
            </p>
            <table className={tableCls}>
              <thead className={theadCls}>
                <tr><th className={thCls}>Period</th><th className={`${thCls} text-right`}>Orders</th><th className={`${thCls} text-right`}>Revenue</th><th className={`${thCls} text-right`}>GST</th><th className={`${thCls} text-right`}>AOV</th></tr>
              </thead>
              <tbody>
                {sales.data.buckets.slice(-12).reverse().map((b) => (
                  <tr key={b.period} className={trCls}>
                    <td className={`${tdCls} text-indigo-200/70`}>{b.period}</td>
                    <td className={`${tdCls} text-right`}>{b.orders}</td>
                    <td className={`${tdCls} text-right text-turmeric-400`}>{inr(b.revenue)}</td>
                    <td className={`${tdCls} text-right`}>{inr(b.gst)}</td>
                    <td className={`${tdCls} text-right`}>{inr(b.aov)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section>
        <div className="mb-2 flex items-center gap-3">
          <ReportHeading>Forecast vs actual (dishes/day)</ReportHeading>
          {forecast.data?.model_version && (
            <span className="ai-meta">🤖 model {forecast.data.model_version}</span>
          )}
        </div>
        <ErrorBar msg={forecast.error} />
        {forecast.data && forecast.data.points.length === 0 && (
          <EmptyState>No forecasts yet — the nightly scoring job (02:00 IST) hasn&apos;t run.</EmptyState>
        )}
        {forecast.data && forecast.data.points.length > 0 && (
          <ForecastChart report={forecast.data} />
        )}
        {forecast.data && forecast.data.dish_anomalies.length > 0 && (
          <div className="mt-3">
            <Eyebrow className="mb-1 text-[#FF8B8B]">Anomalies</Eyebrow>
            {forecast.data.dish_anomalies.map((a) => (
              <p key={`${a.item_id}-${a.date}`} className="tnum text-xs text-indigo-200">
                <span className="text-[#FF8B8B]">⚑</span> {a.date} — {a.name}: forecast{" "}
                {a.forecast_qty}, actual {a.actual_qty} ({a.deviation_pct}% off)
              </p>
            ))}
          </div>
        )}
      </section>

      <section>
        <div className="mb-2">
          <ReportHeading>Dish P&L (last 30d)</ReportHeading>
        </div>
        <ErrorBar msg={pnl.error} />
        {pnl.data && (
          <table className={tableCls}>
            <thead className={theadCls}>
              <tr>
                <th className={thCls}>Dish</th><th className={`${thCls} text-right`}>Qty</th><th className={`${thCls} text-right`}>Revenue</th>
                <th className={`${thCls} text-right`}>Ingredient cost</th><th className={`${thCls} text-right`}>Margin</th><th className={`${thCls} text-right`}>Margin %</th>
              </tr>
            </thead>
            <tbody>
              {pnl.data.rows.slice(0, 20).map((r) => (
                <tr key={r.item_id} className={trCls}>
                  <td className={tdCls}>
                    {r.name} <span className="text-indigo-200/60">{r.category}</span>
                  </td>
                  <td className={`${tdCls} text-right`}>{r.qty}</td>
                  <td className={`${tdCls} text-right text-turmeric-400`}>{inr(r.revenue)}</td>
                  <td className={`${tdCls} text-right`}>
                    {inr(r.ingredient_cost)}{" "}
                    {r.cost_source === "estimated" && (
                      <span title="No priced recipe — 35% food-cost estimate" className="text-indigo-200/60">
                        est.
                      </span>
                    )}
                  </td>
                  <td className={`${tdCls} text-right ${r.margin >= 0 ? "text-[#5BD69B]" : "text-[#FF8B8B]"}`}>{inr(r.margin)}</td>
                  <td className={`${tdCls} text-right`}>{r.margin_pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <div className="mb-2">
          <ReportHeading>GST export</ReportHeading>
        </div>
        <ErrorBar msg={csvError} />
        <div className="flex items-center gap-2">
          <Input
            tone="dark"
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="px-2 py-1"
          />
          <Btn variant="gold" size="sm" onClick={downloadGst}>Download CSV</Btn>
        </div>
      </section>
    </div>
  );
}

/** Dependency-free SVG chart: actual bars, forecast line, anomaly dots. */
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
  /* Token hex literals for SVG paint (Tailwind arbitrary fills are unreliable
     with CSS-var tokens): #F2B705 = turmeric-500, #B9B6D9 = indigo-200, #D64545 = chili. */
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-3xl rounded-lg bg-indigo-950/60">
      <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="rgba(255,255,255,0.05)" />
      {points.map((p, i) =>
        p.actual_qty === null ? null : (
          <rect
            key={p.date}
            x={x(i) - barW / 2}
            y={y(p.actual_qty)}
            width={barW}
            height={H - pad - y(p.actual_qty)}
            fill="#F2B705"
            fillOpacity={0.8}
          />
        ),
      )}
      <polyline points={line} fill="none" strokeWidth={2} stroke="#B9B6D9" />
      {points.map((p, i) =>
        p.anomaly && p.actual_qty !== null ? (
          <circle key={`a-${p.date}`} cx={x(i)} cy={y(p.actual_qty)} r={4} fill="#D64545">
            <title>{`${p.date}: forecast ${p.forecast_qty}, actual ${p.actual_qty}`}</title>
          </circle>
        ) : null,
      )}
      <text x={pad} y={12} fill="#B9B6D9" className="text-[10px]">
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
      <EmptyState>No segments yet — the nightly CRM scoring job (03:00 IST) hasn&apos;t run.</EmptyState>
    );
  }
  const tiers = (data?.tiers ?? [])
    .slice()
    .sort((a, b) => TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier));
  return (
    <div>
      <ErrorBar msg={error} />
      <div className="mb-2 flex items-center gap-3">
        <ReportHeading>Segments</ReportHeading>
        {data?.computed_at && (
          <span className="text-xs text-indigo-200/60">
            scored {new Date(data.computed_at).toLocaleString()}
          </span>
        )}
        <Btn variant="ghost" size="sm" onClick={refresh}>↻</Btn>
      </div>
      <div className="mb-6 flex flex-wrap gap-3">
        {tiers.map((t) => (
          <div key={t.tier} className="rounded-lg bg-indigo-800 px-4 py-3">
            <Eyebrow>{t.tier}</Eyebrow>
            <p className="tnum font-display text-lg font-semibold text-turmeric-400">{t.users}</p>
            <p className="tnum text-xs text-indigo-200/70">
              LTV {inr(t.total_ltv)} · churn {(t.avg_churn_risk * 100).toFixed(0)}%
            </p>
          </div>
        ))}
      </div>
      <div className="mb-2">
        <ReportHeading>
          Win-back targets <span className="font-sans text-xs font-normal text-indigo-200/60">(high LTV × churn risk)</span>
        </ReportHeading>
      </div>
      <table className={tableCls}>
        <thead className={theadCls}>
          <tr><th className={thCls}>Customer</th><th className={thCls}>Tier</th><th className={`${thCls} text-right`}>Churn risk</th><th className={`${thCls} text-right`}>LTV</th></tr>
        </thead>
        <tbody>
          {(data?.at_risk ?? []).map((u) => (
            <tr key={u.user_id} className={trCls}>
              <td className={tdCls}>
                {u.name ?? "—"} <span className="font-mono text-indigo-200/60">{u.phone}</span>
              </td>
              <td className={tdCls}>{u.rfm_tier}</td>
              <td className={`${tdCls} text-right ${u.churn_risk >= 0.8 ? "text-[#FF8B8B]" : "text-turmeric-400"}`}>
                {(u.churn_risk * 100).toFixed(0)}%
              </td>
              <td className={`${tdCls} text-right`}>{inr(u.ltv)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
