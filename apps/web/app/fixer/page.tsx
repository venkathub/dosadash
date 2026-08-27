"use client";

/**
 * /fixer — Fixer Ops portal (Phase 14 slice 4).
 *
 * The self-healing loop's own surface (KDS pattern: dedicated route, own
 * token, live WebSocket): a pipeline board of every feedback report from
 * intake to prod-verified, inline approve/reject, per-report timeline
 * drill-down, and the slice-3 metrics rollup. Admin/owner JWT; all data
 * comes from the existing /admin/feedback endpoints — the socket
 * (/ws/fixer) is only a refresh trigger + live feed, so REST and WS can
 * never tell different stories.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Badge,
  Btn,
  Card,
  EmptyState,
  ErrorBar,
  Input,
  Modal,
  cx,
  type BadgeTone,
} from "../components/ui";

/* ------------------------------------------------------------- types */

type Report = {
  id: number;
  reporter_tier: "ANON" | "CUSTOMER" | "STAFF" | "SYSTEM";
  type: "BUG" | "FEATURE";
  status: string;
  title: string;
  description: string;
  github_issue_number: number | null;
  github_error: string | null;
  triage: {
    verdict?: string;
    fallback?: boolean;
    model?: string | null;
    assessment?: { summary?: string; effort?: string; risk?: string; area?: string } | null;
  } | null;
  fix_pr_number: number | null;
  verified_at: string | null;
  created_at: string;
  updated_at: string;
};

type ReportList = { reports: Report[]; total: number; github_repo: string };

type LifecycleEvent = {
  id: number;
  stage: string;
  actor: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
};

type Metrics = {
  window_days: number;
  totals_by_status: Record<string, number>;
  funnel: Record<string, number>;
  rates: Record<string, number | null>;
  latency: Record<string, { p50: number | null; p90: number | null; count: number }>;
  weekly: { week: string; reports: number; fixed: number; verified: number }[];
  runs: Record<string, Record<string, number>>;
  spend?: Record<string, number | null>;
};

type Ops = {
  github_actions: { status: string; incident: string | null; checked_at: string } | null;
  stalls: {
    report_id: number;
    reason: string;
    run_id: number | null;
    retries: number;
    since: string | null;
  }[];
  watchdog_enabled: boolean;
};

/* ------------------------------------------------------- api helper */

const TOKEN_KEY = "fixer_token";

async function api<T>(
  token: string,
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const resp = await fetch(`/api/v1${path}`, {
    method: opts.method ?? "GET",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  const data = await resp.json().catch(() => null);
  if (!resp.ok)
    throw new Error((data as { detail?: string } | null)?.detail ?? `HTTP ${resp.status}`);
  return data as T;
}

/* ------------------------------------------------------ presentation */

const LANES: { label: string; statuses: string[]; accent: string }[] = [
  { label: "📥 Intake", statuses: ["RECEIVED", "TRACKED"], accent: "bg-sky" },
  { label: "🟡 Approval", statuses: ["NEEDS_APPROVAL"], accent: "bg-turmeric-500" },
  { label: "🤖 Fixing", statuses: ["AUTO_FIX", "APPROVED", "FIXING"], accent: "bg-magenta-500" },
  { label: "🔀 PR open", statuses: ["PR_OPEN"], accent: "bg-indigo-200" },
  { label: "🚀 Shipped", statuses: ["FIXED", "VERIFIED"], accent: "bg-veg" },
  { label: "⚠️ Attention", statuses: ["REOPENED"], accent: "bg-chili" },
];

const CLOSED_STATUSES = ["REJECTED", "DISMISSED"];

function fixerTone(status: string): BadgeTone {
  switch (status) {
    case "RECEIVED":
    case "NEEDS_APPROVAL":
      return "warning";
    case "TRACKED":
    case "FIXING":
    case "PR_OPEN":
      return "info";
    case "AUTO_FIX":
      return "brass";
    case "APPROVED":
    case "FIXED":
    case "VERIFIED":
      return "success";
    case "REJECTED":
    case "REOPENED":
      return "danger";
    default:
      return "neutral";
  }
}

/** Mirror of the Telegram card vocabulary (bot render.py). */
const STAGE_LABEL: Record<string, string> = {
  RECEIVED: "📥 Received",
  TRACKED: "📌 Filed on GitHub",
  TRIAGED: "🔎 Triaged",
  APPROVED: "✅ Approved",
  REJECTED: "🚫 Rejected",
  FIX_STARTED: "🤖 AI fixer dispatched",
  FIX_STALLED: "⏳ Fixer run stalled on GitHub",
  FIX_RETRIED: "🔁 Fixer re-dispatched by watchdog",
  RCA_POSTED: "🧠 Root cause posted",
  ESCALATED: "🛑 Fixer escalated",
  FIX_FAILED: "💥 Fixer run failed",
  PR_OPENED: "🔀 Fix PR opened",
  PR_CLOSED: "❌ Fix PR closed unmerged",
  PR_MERGED: "🎉 Fix PR merged",
  FIXED: "🧩 Fix landed",
  VERIFICATION_POSTED: "🧪 Prod verification posted",
  VERIFIED: "🏁 Verified live in prod",
  REOPENED: "⚠️ Reopened",
  CLOSED: "🗂 Issue closed",
  DISMISSED: "🚮 Dismissed",
  SYNCED: "🔁 Synced from GitHub",
};

const TIER_ICON: Record<Report["reporter_tier"], string> = {
  ANON: "👤",
  CUSTOMER: "🛒",
  STAFF: "🧑‍🍳",
  SYSTEM: "🛰️",
};

/** DB timestamps are naive UTC — pin the zone before Date.parse. */
function parseUtc(iso: string): number {
  return Date.parse(/[Zz+]/.test(iso.slice(10)) ? iso : `${iso}Z`);
}

function ago(iso: string, now: number): string {
  const mins = Math.max(0, Math.floor((now - parseUtc(iso)) / 60_000));
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 48) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function stamp(iso: string): string {
  return new Date(parseUtc(iso)).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function humanSeconds(s: number | null): string {
  if (s === null) return "—";
  if (s < 90) return `${Math.round(s)}s`;
  const mins = s / 60;
  if (mins < 90) return `${Math.round(mins)}m`;
  const hours = mins / 60;
  if (hours < 36) return `${Math.round(hours * 10) / 10}h`;
  return `${Math.round((hours / 24) * 10) / 10}d`;
}

function pct(r: number | null): string {
  return r === null ? "—" : `${Math.round(r * 1000) / 10}%`;
}

/** Display-only: read the role claim out of the stored JWT for the header chip
 *  (same pattern as the /admin shell — never used for authorization). */
function roleFromToken(token: string): string | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return typeof payload.role === "string" ? payload.role : null;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------ page */

export default function FixerPortal() {
  const [token, setToken] = useState<string | null>(null);
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [demoOtp, setDemoOtp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Mount-gate ALL localStorage reads (issue-#120 hydration lesson).
  useEffect(() => {
    setToken(localStorage.getItem(TOKEN_KEY));
  }, []);

  const requestOtp = async () => {
    setError(null);
    try {
      const r = await fetch("/api/v1/auth/otp/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone }),
      });
      // A mid-deploy 502 from the proxy has an empty/HTML body — .json()
      // would throw and leave the button silently dead (caught live in prod).
      const body = await r.json().catch(() => null);
      if (!r.ok) return setError(body?.detail ?? `OTP request failed (HTTP ${r.status}) — retry in a moment`);
      setDemoOtp(body?.demo_otp ?? null);
    } catch {
      setError("Network error — check your connection and retry.");
    }
  };

  const verifyOtp = async () => {
    setError(null);
    try {
      const r = await fetch("/api/v1/auth/otp/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone, otp }),
      });
      const body = await r.json().catch(() => null);
      if (!r.ok) return setError(body?.detail ?? `Verification failed (HTTP ${r.status}) — retry in a moment`);
      if (body.user.role !== "admin" && body.user.role !== "owner") {
        return setError(`This account has role '${body.user.role}' — Fixer Ops needs admin access.`);
      }
      localStorage.setItem(TOKEN_KEY, body.access_token);
      setToken(body.access_token);
    } catch {
      setError("Network error — check your connection and retry.");
    }
  };

  if (!token) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-indigo-950 text-indigo-100">
        <Card tone="dark" className="w-80 space-y-3 p-6">
          <div className="font-display text-lg font-bold tracking-wide text-white">
            🛠 DOSADASH <span className="text-magenta-400">FIXER OPS</span>
          </div>
          <p className="text-sm text-indigo-200">
            The self-healing loop, live. Admin or owner sign-in.
          </p>
          <Input
            tone="dark"
            className="w-full py-2"
            placeholder="Phone (+91…)"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && requestOtp()}
          />
          {demoOtp === null ? (
            <Btn variant="turmeric" className="w-full py-2" onClick={requestOtp}>
              Send OTP
            </Btn>
          ) : (
            <>
              <p className="rounded-lg border-[1.5px] border-turmeric-600 bg-turmeric-500/15 px-2 py-1 text-xs text-turmeric-400">
                Demo OTP: <b className="font-display">{demoOtp}</b>
              </p>
              <Input
                tone="dark"
                className="w-full py-2"
                placeholder="6-digit OTP"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && otp.length === 6 && verifyOtp()}
              />
              <Btn
                variant="turmeric"
                className="w-full py-2"
                disabled={otp.length !== 6}
                onClick={verifyOtp}
              >
                Sign in
              </Btn>
            </>
          )}
          <ErrorBar msg={error} />
        </Card>
      </main>
    );
  }

  return <Board token={token} onLogout={() => {
    localStorage.removeItem(TOKEN_KEY);
    setToken(null);
  }} />;
}

/* ------------------------------------------------------------ board */

function Board({ token, onLogout }: { token: string; onLogout: () => void }) {
  const [reports, setReports] = useState<Report[]>([]);
  const [repo, setRepo] = useState("");
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [ops, setOps] = useState<Ops | null>(null);
  const [feed, setFeed] = useState<{ at: number; text: string }[]>([]);
  const [connected, setConnected] = useState(false);
  const [selected, setSelected] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const wsRef = useRef<WebSocket | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [list, m] = await Promise.all([
        api<ReportList>(token, "/admin/feedback?limit=100"),
        api<Metrics>(token, "/admin/feedback/metrics"),
      ]);
      setReports(list.reports);
      setRepo(list.github_repo);
      setMetrics(m);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "load failed");
    }
    // Loop health is additive transparency — its failure must never blank
    // the board (older api without /ops, transient 5xx…).
    try {
      setOps(await api<Ops>(token, "/admin/feedback/ops"));
    } catch {
      setOps(null);
    }
  }, [token]);

  useEffect(() => {
    refresh();
    const poll = setInterval(refresh, 60_000); // WS fallback
    const tick = setInterval(() => setNow(Date.now()), 30_000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [refresh]);

  // Live socket: every lifecycle stage lands here → debounce a refetch.
  useEffect(() => {
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws/fixer?token=${token}`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onmessage = (msg) => {
        try {
          const event = JSON.parse(msg.data) as { type: string; report_id?: number };
          if (event.type === "feedback.hello") return;
          setFeed((prev) =>
            [
              {
                at: Date.now(),
                text: `#${event.report_id} · ${
                  STAGE_LABEL[event.type.replace("feedback.", "").toUpperCase()] ?? event.type
                }`,
              },
              ...prev,
            ].slice(0, 12),
          );
          if (refreshTimer.current) clearTimeout(refreshTimer.current);
          refreshTimer.current = setTimeout(refresh, 400);
        } catch {
          /* non-JSON frame — ignore */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 3000);
      };
    };
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      wsRef.current?.close();
    };
  }, [token, refresh]);

  const decide = async (report: Report, action: "approve" | "reject") => {
    try {
      await api(token, `/admin/feedback/${report.id}/decision`, {
        method: "POST",
        body: { action },
      });
      setSelected(null);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "decision failed");
    }
  };

  const closedReports = reports.filter((r) => CLOSED_STATUSES.includes(r.status));
  const role = roleFromToken(token);
  const stalledIds = new Set((ops?.stalls ?? []).map((s) => s.report_id));

  return (
    <main className="min-h-screen bg-indigo-950 pb-16 text-indigo-100">
      {/* Backoffice-family shell: indigo-900 header + 3px magenta accent
          (magenta = admin surfaces; turmeric stays customer/KDS — docs/13 §3). */}
      <header className="sticky top-0 z-20 border-b-[3px] border-magenta-500 bg-indigo-900 px-4 py-3">
        <div className="mx-auto flex max-w-[1380px] items-center justify-between gap-3">
          <div className="flex items-baseline gap-3">
            <span className="font-display text-lg font-bold tracking-wide text-white">
              🛠 DOSADASH <span className="text-magenta-400">FIXER OPS</span>
            </span>
            {connected ? (
              <span className="font-display text-[12px] font-bold uppercase tracking-[0.1em] text-[#5BD69B]">
                <span className="animate-pulse-soft">●</span> live
              </span>
            ) : (
              <span className="font-display text-[12px] font-bold uppercase tracking-[0.1em] text-turmeric-400">
                ○ reconnecting…
              </span>
            )}
          </div>
          <div className="flex items-center gap-2.5">
            {role && <Badge tone="brass">{role}</Badge>}
            <a href="/admin" className="text-xs text-indigo-200 underline-offset-2 hover:underline">
              Backoffice ↗
            </a>
            <Btn variant="ghost" size="sm" onClick={onLogout}>
              Logout
            </Btn>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1380px] px-4">
        <ErrorBar msg={error} />

        {ops && <LoopHealthBanner ops={ops} />}

        {metrics && <MetricsStrip metrics={metrics} />}

        <div className="mt-6 grid items-start gap-3 md:grid-cols-3 xl:grid-cols-6">
          {LANES.map((lane) => {
            const laneReports = reports.filter((r) => lane.statuses.includes(r.status));
            return (
              <section
                key={lane.label}
                className="min-w-0 rounded-xl border-2 border-indigo-700 bg-indigo-900 p-3"
              >
                <div className="mb-3 flex items-center justify-between gap-2 px-1">
                  <h2 className="font-display text-[11px] font-bold uppercase tracking-[0.14em] text-turmeric-400">
                    {lane.label}
                  </h2>
                  <span className="tnum min-w-[30px] rounded-full bg-turmeric-500 px-2.5 text-center font-display text-[13px] font-bold text-indigo-900">
                    {laneReports.length}
                  </span>
                </div>
                <div className="grid gap-2">
                  {laneReports.length === 0 && (
                    <div className="rounded-lg border-2 border-dashed border-indigo-600 p-3 text-center text-xs text-indigo-300">
                      —
                    </div>
                  )}
                  {laneReports.map((report) => (
                    <div
                      key={report.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => setSelected(report)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") setSelected(report);
                      }}
                      className="relative cursor-pointer overflow-hidden rounded-lg border-2 border-ink bg-offwhite p-3 pl-4 text-left text-ink shadow-pop-dark transition-transform hover:-translate-y-0.5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-magenta-500"
                    >
                      <span className={cx("absolute inset-y-0 left-0 w-[6px]", lane.accent)} />
                      <div className="flex items-center justify-between gap-2">
                        <span className="tnum text-xs font-bold">
                          {report.type === "BUG" ? "🐞" : "✨"} #{report.id}
                        </span>
                        <span className="flex items-center gap-1">
                          {stalledIds.has(report.id) && (
                            <Badge
                              tone="warning"
                              surface="light"
                              title="Fixer dispatch stalled on GitHub — the watchdog will auto-retry"
                            >
                              ⏳ stalled
                            </Badge>
                          )}
                          <Badge tone={fixerTone(report.status)} surface="light">
                            {report.status}
                          </Badge>
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-2 text-[13px] font-semibold">{report.title}</p>
                      <p className="mt-1 text-[11px] text-muted">
                        {TIER_ICON[report.reporter_tier]} {ago(report.created_at, now)}
                        {report.github_issue_number !== null && ` · issue #${report.github_issue_number}`}
                        {report.fix_pr_number !== null && ` · PR #${report.fix_pr_number}`}
                      </p>
                      {report.status === "NEEDS_APPROVAL" && (
                        <div className="mt-2 flex gap-2">
                          <Btn
                            size="sm"
                            variant="veg"
                            onClick={(e) => {
                              e.stopPropagation();
                              decide(report, "approve");
                            }}
                          >
                            Approve
                          </Btn>
                          <Btn
                            size="sm"
                            variant="paper"
                            onClick={(e) => {
                              e.stopPropagation();
                              decide(report, "reject");
                            }}
                          >
                            Reject
                          </Btn>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </section>
            );
          })}
        </div>

        {feed.length > 0 && (
          <section className="mt-8">
            <h2 className="mb-2 font-display text-[11px] font-bold uppercase tracking-[0.16em] text-turmeric-400">
              📡 Live feed
            </h2>
            <div className="grid gap-1">
              {feed.map((entry) => (
                <p key={`${entry.at}-${entry.text}`} className="text-xs text-indigo-100">
                  <span className="tnum text-indigo-300">
                    {new Date(entry.at).toLocaleTimeString("en-IN", {
                      timeZone: "Asia/Kolkata",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </span>{" "}
                  {entry.text}
                </p>
              ))}
            </div>
          </section>
        )}

        {closedReports.length > 0 && (
          <section className="mt-8">
            <h2 className="mb-2 font-display text-[11px] font-bold uppercase tracking-[0.16em] text-indigo-300">
              🗂 Closed ({closedReports.length})
            </h2>
            <div className="flex flex-wrap gap-2">
              {closedReports.map((report) => (
                <button
                  key={report.id}
                  type="button"
                  onClick={() => setSelected(report)}
                  className="rounded-md border border-indigo-600 px-2 py-1 text-xs text-indigo-200 hover:border-indigo-300"
                >
                  #{report.id} {report.title.slice(0, 40)} · {report.status}
                </button>
              ))}
            </div>
          </section>
        )}

        {reports.length === 0 && (
          <EmptyState>
            No feedback reports yet — customer 🐞 reports land here the moment they arrive.
          </EmptyState>
        )}
      </div>

      {selected && (
        <ReportDrawer
          token={token}
          report={selected}
          repo={repo}
          onClose={() => setSelected(null)}
          onDecide={decide}
        />
      )}
    </main>
  );
}

/* --------------------------------------------------------- metrics */

/** Human vocabulary for watchdog stall reasons (payload.reason). */
const STALL_REASON: Record<string, string> = {
  run_queued: "run stuck in GitHub's queue",
  run_died: "run died before starting (startup failure)",
  dispatch_lost: "GitHub never started a run",
  cancel_forbidden: "stuck run can't be cancelled (token lacks actions:write)",
  retries_exhausted: "auto-retry limit reached — needs a human",
};

/** Loop-health transparency (Actions-outage postmortem): when GitHub is
 *  down or a dispatch stalled, say so — the board must never look idle
 *  while a fix is silently going nowhere. */
function LoopHealthBanner({ ops }: { ops: Ops }) {
  const gh = ops.github_actions;
  const outage = gh !== null && gh.status !== "operational";
  if (!outage && ops.stalls.length === 0) return null;
  return (
    <div className="mt-5 grid gap-2">
      {outage && gh && (
        <div className="rounded-lg border-2 border-chili bg-chili/15 px-3 py-2 text-sm text-[#FF8B8B]">
          <span className="font-display font-bold">
            🛑 GitHub Actions: {gh.status.replace(/_/g, " ")}
          </span>
          {gh.incident && <span className="text-indigo-100"> — “{gh.incident}”</span>}
          <span className="block text-xs text-indigo-200">
            Fix runs can’t execute right now. The watchdog is tracking this and will
            re-dispatch stalled fixes automatically once GitHub recovers.
          </span>
        </div>
      )}
      {ops.stalls.map((stall) => (
        <div
          key={stall.report_id}
          className="rounded-lg border-2 border-turmeric-600 bg-turmeric-500/10 px-3 py-2 text-sm text-turmeric-400"
        >
          <span className="font-display font-bold">
            ⏳ Report #{stall.report_id} — fixer dispatch stalled
          </span>
          <span className="block text-xs text-indigo-200">
            {STALL_REASON[stall.reason] ?? stall.reason}
            {stall.run_id !== null && ` (run ${stall.run_id})`} · retries {stall.retries}/3
            {stall.since && ` · since ${stamp(stall.since)}`}
            {stall.reason === "retries_exhausted"
              ? " · auto-retry stopped — re-approve or investigate the workflow"
              : " · auto-retry armed"}
          </span>
        </div>
      ))}
    </div>
  );
}

function MetricsStrip({ metrics }: { metrics: Metrics }) {
  // Report/verified counts come from totals_by_status (current status of every
  // report in the window) — NOT the event funnel: reports that predate the
  // feedback_events table have no lifecycle events, so the funnel undercounts
  // history and would contradict the board right next to it.
  const totalReports = Object.values(metrics.totals_by_status ?? {}).reduce((a, b) => a + b, 0);
  const cards: { label: string; value: string; sub?: string }[] = [
    {
      label: "Reports",
      value: String(totalReports),
      sub: `${metrics.window_days}d window`,
    },
    { label: "Auto-fix rate", value: pct(metrics.rates.auto_fix_rate) },
    { label: "Merge rate", value: pct(metrics.rates.merge_rate) },
    {
      label: "Verified",
      value: String(metrics.totals_by_status?.VERIFIED ?? 0),
      sub: `reopen ${pct(metrics.rates.reopen_rate)}`,
    },
    {
      label: "Approval latency",
      value: humanSeconds(metrics.latency.approval_latency?.p50 ?? null),
      sub: `p90 ${humanSeconds(metrics.latency.approval_latency?.p90 ?? null)}`,
    },
    {
      label: "MTTR → verified",
      value: humanSeconds(metrics.latency.mttr_received_to_verified?.p50 ?? null),
      sub: `${metrics.latency.mttr_received_to_verified?.count ?? 0} samples`,
    },
    {
      label: "Fix runs",
      value: `${metrics.runs.fix?.success ?? 0}/${metrics.runs.fix?.total ?? 0} ok`,
      sub: `verify ${metrics.runs.verify?.total ?? 0}`,
    },
    {
      // Phase 15 S7: loop TCO + within-run prompt-cache share from run
      // telemetry. "—" until a run reports usage (honest null, never 0).
      label: "Agent spend",
      value:
        metrics.spend?.total_cost_usd != null
          ? `$${metrics.spend.total_cost_usd.toFixed(2)}`
          : "—",
      sub: `cached ${pct(metrics.rates.fix_cached_token_share ?? null)}`,
    },
  ];
  return (
    <section className="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
      {cards.map((card) => (
        <div
          key={card.label}
          className="rounded-xl border-2 border-indigo-700 bg-indigo-900 p-3"
        >
          <p className="font-display text-[10.5px] font-bold uppercase tracking-[0.12em] text-turmeric-400">
            {card.label}
          </p>
          <p className="tnum font-display text-xl font-bold text-white">{card.value}</p>
          {card.sub && <p className="tnum text-[11px] text-indigo-300">{card.sub}</p>}
        </div>
      ))}
    </section>
  );
}

/* ---------------------------------------------------------- drawer */

function ReportDrawer({
  token,
  report,
  repo,
  onClose,
  onDecide,
}: {
  token: string;
  report: Report;
  repo: string;
  onClose: () => void;
  onDecide: (report: Report, action: "approve" | "reject") => void;
}) {
  const [events, setEvents] = useState<LifecycleEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ events: LifecycleEvent[] }>(token, `/admin/feedback/${report.id}/events`)
      .then((body) => setEvents(body.events))
      .catch((e) => setError(e instanceof Error ? e.message : "timeline load failed"));
  }, [token, report.id]);

  const assessment = report.triage?.assessment ?? null;

  return (
    <Modal onClose={onClose} className="w-full max-w-xl">
      <div className="grid max-h-[80vh] gap-4 overflow-y-auto p-5">
        <div>
          <p className="tnum text-xs font-bold text-muted">
            {report.type === "BUG" ? "🐞 Bug" : "✨ Feature"} report #{report.id} ·{" "}
            {TIER_ICON[report.reporter_tier]} {report.reporter_tier.toLowerCase()}
          </p>
          <h3 className="font-display text-lg font-bold">{report.title}</h3>
          <p className="mt-1 whitespace-pre-wrap text-sm text-muted">{report.description}</p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={fixerTone(report.status)} surface="light">
            {report.status}
          </Badge>
          {report.github_issue_number !== null && repo && (
            <a
              className="text-xs text-magenta-600 underline-offset-2 hover:underline"
              href={`https://github.com/${repo}/issues/${report.github_issue_number}`}
              target="_blank"
              rel="noreferrer"
            >
              issue #{report.github_issue_number} ↗
            </a>
          )}
          {report.fix_pr_number !== null && repo && (
            <a
              className="text-xs text-magenta-600 underline-offset-2 hover:underline"
              href={`https://github.com/${repo}/pull/${report.fix_pr_number}`}
              target="_blank"
              rel="noreferrer"
            >
              PR #{report.fix_pr_number} ↗
            </a>
          )}
          {report.github_error && (
            <span className="text-[11px] text-chili">mirror: {report.github_error}</span>
          )}
        </div>

        {assessment && (
          <p className="ai-meta text-xs">
            🤖 {assessment.summary}{" "}
            <span className="text-muted">
              (effort {assessment.effort ?? "?"} · risk {assessment.risk ?? "?"} ·{" "}
              {report.triage?.model ?? "policy"}
              {report.triage?.fallback ? " · fallback" : ""})
            </span>
          </p>
        )}

        {report.status === "NEEDS_APPROVAL" && (
          <div className="flex gap-2">
            <Btn variant="veg" onClick={() => onDecide(report, "approve")}>
              ✅ Approve fix
            </Btn>
            <Btn variant="paper" onClick={() => onDecide(report, "reject")}>
              🚫 Reject
            </Btn>
          </div>
        )}

        <div>
          <h4 className="mb-2 font-display text-[13px] font-bold uppercase tracking-[0.1em]">
            Timeline
          </h4>
          <ErrorBar msg={error} />
          {events === null && !error && <p className="text-xs text-muted">Loading…</p>}
          {events !== null && events.length === 0 && (
            <p className="text-xs text-muted">No lifecycle events recorded yet.</p>
          )}
          {events !== null && (
            <ol className="grid gap-1.5">
              {events.map((event) => (
                <li key={event.id} className="flex items-baseline gap-2 text-[13px]">
                  <span className="tnum shrink-0 text-[11px] text-muted">
                    {stamp(event.created_at)}
                  </span>
                  <span>{STAGE_LABEL[event.stage] ?? event.stage}</span>
                  {event.payload?.verdict !== undefined && (
                    <span className="text-[11px] text-muted">({String(event.payload.verdict)})</span>
                  )}
                  {event.payload?.reason !== undefined && (
                    <span className="text-[11px] text-muted">({String(event.payload.reason)})</span>
                  )}
                  {event.payload?.attempt !== undefined && (
                    <span className="text-[11px] text-muted">
                      (attempt {String(event.payload.attempt)})
                    </span>
                  )}
                  {event.payload?.pr_number !== undefined && event.payload?.pr_number !== null && (
                    <span className="text-[11px] text-muted">
                      (PR #{String(event.payload.pr_number)})
                    </span>
                  )}
                  {event.actor && (
                    <span className="ml-auto shrink-0 text-[10px] uppercase tracking-wide text-muted">
                      {event.actor}
                    </span>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </Modal>
  );
}
