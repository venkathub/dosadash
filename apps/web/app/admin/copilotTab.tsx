"use client";

/** Analytics copilot tab (Phase 5): NL question → guarded SQL → table + chart. */

import { useState } from "react";
import {
  Btn,
  Chip,
  Input,
  tableCls,
  tdCls,
  thCls,
  theadCls,
  trCls,
} from "../components/ui";
import { adminApi } from "./adminApi";
import { ErrorBar } from "./tabs";

type Chart = { type: "bar" | "line" | "none"; x: string; y: string };
type Cell = string | number | boolean | null;
type Answer = {
  question: string;
  sql: string | null;
  explanation: string | null;
  columns: string[];
  rows: Cell[][];
  row_count: number;
  truncated: boolean;
  chart: Chart;
  attempts: number;
  model: string | null;
  error: string | null;
};

const SUGGESTIONS = [
  "Top 5 dishes by revenue last 30 days",
  "Daily orders this month",
  "How many customers are at risk of churning?",
  "Which dishes are forecast to sell most this week?",
  "Weekend vs weekday biryani sales last month",
];

export function CopilotTab() {
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState("");

  const submit = async (q: string) => {
    if (!q.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      setAnswer(await adminApi<Answer>("/admin/copilot/ask", { method: "POST", body: { question: q } }));
    } catch {
      setError("Copilot unavailable");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-4xl">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit(question);
        }}
        className="mb-3 flex gap-2"
      >
        <Input
          tone="dark"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about sales, dishes, forecasts, customers…"
          className="flex-1 px-3 py-2"
        />
        <Btn variant="turmeric" size="md" disabled={busy || question.trim().length < 3}>
          {busy ? "Thinking…" : "Ask"}
        </Btn>
      </form>
      <div className="mb-4 flex flex-wrap gap-2">
        {SUGGESTIONS.map((s) => (
          <Chip
            key={s}
            surface="dark"
            onClick={() => {
              setQuestion(s);
              void submit(s);
            }}
          >
            {s}
          </Chip>
        ))}
      </div>
      <ErrorBar msg={error} />

      {answer?.error && (
        <p className="rounded-lg border border-chili/40 bg-chili/15 px-3 py-2 text-sm text-[#FF8B8B]">
          ⚠ {answer.error} (after {answer.attempts} attempt{answer.attempts > 1 ? "s" : ""})
        </p>
      )}

      {answer && !answer.error && (
        <div className="space-y-4">
          <p className="text-sm text-indigo-200">
            {answer.explanation}{" "}
            <span className="ai-meta">
              🤖 {answer.model} · attempt {answer.attempts} · {answer.row_count} row{answer.row_count === 1 ? "" : "s"}
              {answer.truncated && " (truncated)"}
            </span>
          </p>

          {answer.chart.type !== "none" && answer.rows.length > 1 && (
            <CopilotChart answer={answer} />
          )}

          <div className="max-h-96 overflow-auto rounded-lg border border-white/5">
            <table className={tableCls}>
              <thead className={`${theadCls} sticky top-0 bg-indigo-800`}>
                <tr>{answer.columns.map((c) => <th key={c} className={thCls}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {answer.rows.map((row, i) => (
                  <tr key={i} className={trCls}>
                    {row.map((cell, j) => (
                      <td key={j} className={`${tdCls} text-indigo-200 ${typeof cell === "number" ? "text-right" : ""}`}>{cell === null ? "—" : String(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <details className="text-xs text-indigo-200/70">
            <summary className="cursor-pointer transition-colors duration-150 hover:text-turmeric-400">Show SQL</summary>
            <pre className="mt-2 overflow-auto rounded-lg bg-indigo-950/60 p-3 font-mono text-indigo-200">{answer.sql}</pre>
          </details>
        </div>
      )}
    </div>
  );
}

/** Dependency-free SVG bar/line chart over the copilot's chosen x/y columns. */
function CopilotChart({ answer }: { answer: Answer }) {
  const xi = answer.columns.indexOf(answer.chart.x);
  const yi = answer.columns.indexOf(answer.chart.y);
  if (yi < 0) return null;
  const points = answer.rows
    .map((r) => ({ x: xi >= 0 ? String(r[xi]) : "", y: Number(r[yi]) }))
    .filter((p) => Number.isFinite(p.y))
    .slice(0, 40);
  if (points.length < 2) return null;

  const W = 640, H = 180, pad = 28;
  const max = Math.max(...points.map((p) => p.y), 1);
  const x = (i: number) => pad + (i * (W - 2 * pad)) / Math.max(1, points.length - 1);
  const y = (v: number) => H - pad - (v / max) * (H - 2 * pad);
  const barW = Math.max(3, (W - 2 * pad) / points.length - 4);

  /* Token hex literals for SVG paint (Tailwind arbitrary fills are unreliable
     with CSS-var tokens): #F2B705 = turmeric-500, #B9B6D9 = indigo-200. */
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded-lg bg-indigo-950/60">
      <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="rgba(255,255,255,0.05)" />
      {answer.chart.type === "bar" ? (
        points.map((p, i) => (
          <rect key={i} x={x(i) - barW / 2} y={y(p.y)} width={barW} height={H - pad - y(p.y)} fill="#F2B705" fillOpacity={0.8}>
            <title>{`${p.x}: ${p.y}`}</title>
          </rect>
        ))
      ) : (
        <polyline
          points={points.map((p, i) => `${x(i)},${y(p.y)}`).join(" ")}
          fill="none"
          strokeWidth={2}
          stroke="#B9B6D9"
        />
      )}
      <text x={pad} y={14} fill="#B9B6D9" className="text-[10px]">
        {answer.chart.y} by {answer.chart.x || "row"} (max {max.toLocaleString()})
      </text>
      <text x={pad} y={H - 8} fill="#B9B6D9" fillOpacity={0.6} className="text-[9px]">{points[0]?.x}</text>
      <text x={W - pad} y={H - 8} textAnchor="end" fill="#B9B6D9" fillOpacity={0.6} className="text-[9px]">
        {points[points.length - 1]?.x}
      </text>
    </svg>
  );
}
