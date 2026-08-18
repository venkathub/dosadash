"use client";

/** Analytics copilot tab (Phase 5): NL question → guarded SQL → table + chart. */

import { useState } from "react";
import { adminApi } from "./adminApi";
import { ErrorBar } from "./tabs";

const btnCls =
  "rounded bg-amber-500 px-3 py-1 text-sm font-semibold text-stone-900 hover:bg-amber-400 disabled:opacity-40";

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
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about sales, dishes, forecasts, customers…"
          className="flex-1 rounded bg-stone-700 px-3 py-2 text-sm text-stone-100 placeholder-stone-400 outline-none focus:ring-1 focus:ring-amber-400"
        />
        <button className={btnCls} disabled={busy || question.trim().length < 3}>
          {busy ? "Thinking…" : "Ask"}
        </button>
      </form>
      <div className="mb-4 flex flex-wrap gap-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s}
            className="rounded-full border border-stone-700 px-3 py-1 text-xs text-stone-400 hover:border-amber-400 hover:text-amber-300"
            onClick={() => {
              setQuestion(s);
              void submit(s);
            }}
          >
            {s}
          </button>
        ))}
      </div>
      <ErrorBar msg={error} />

      {answer?.error && (
        <p className="rounded bg-red-900/60 px-3 py-2 text-sm text-red-200">
          ⚠ {answer.error} (after {answer.attempts} attempt{answer.attempts > 1 ? "s" : ""})
        </p>
      )}

      {answer && !answer.error && (
        <div className="space-y-4">
          <p className="text-sm text-stone-300">
            {answer.explanation}{" "}
            <span className="text-xs text-stone-500">
              — {answer.row_count} row{answer.row_count === 1 ? "" : "s"}
              {answer.truncated && " (truncated)"} · {answer.model} · attempt {answer.attempts}
            </span>
          </p>

          {answer.chart.type !== "none" && answer.rows.length > 1 && (
            <CopilotChart answer={answer} />
          )}

          <div className="max-h-96 overflow-auto rounded border border-stone-800">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-stone-800 uppercase text-stone-400">
                <tr>{answer.columns.map((c) => <th key={c} className="p-2">{c}</th>)}</tr>
              </thead>
              <tbody>
                {answer.rows.map((row, i) => (
                  <tr key={i} className="border-t border-stone-800">
                    {row.map((cell, j) => (
                      <td key={j} className="p-2 text-stone-300">{cell === null ? "—" : String(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <details className="text-xs text-stone-400">
            <summary className="cursor-pointer hover:text-amber-300">Show SQL</summary>
            <pre className="mt-2 overflow-auto rounded bg-stone-800/80 p-3 text-stone-300">{answer.sql}</pre>
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

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full rounded bg-stone-800/60">
      {answer.chart.type === "bar" ? (
        points.map((p, i) => (
          <rect key={i} x={x(i) - barW / 2} y={y(p.y)} width={barW} height={H - pad - y(p.y)} className="fill-amber-400">
            <title>{`${p.x}: ${p.y}`}</title>
          </rect>
        ))
      ) : (
        <polyline
          points={points.map((p, i) => `${x(i)},${y(p.y)}`).join(" ")}
          fill="none"
          strokeWidth={2}
          className="stroke-amber-400"
        />
      )}
      <text x={pad} y={14} className="fill-stone-400 text-[10px]">
        {answer.chart.y} by {answer.chart.x || "row"} (max {max.toLocaleString()})
      </text>
      <text x={pad} y={H - 8} className="fill-stone-500 text-[9px]">{points[0]?.x}</text>
      <text x={W - pad} y={H - 8} textAnchor="end" className="fill-stone-500 text-[9px]">
        {points[points.length - 1]?.x}
      </text>
    </svg>
  );
}
