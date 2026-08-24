"use client";

import { Fragment, useCallback, useState } from "react";

import { adminApi, type AdminFeedback, type AdminFeedbackList } from "./adminApi";
import {
  Badge,
  Btn,
  EmptyState,
  ErrorBar,
  Eyebrow,
  Select,
  tableCls,
  tdCls,
  thCls,
  theadCls,
  trCls,
  type BadgeTone,
} from "../components/ui";
import { useLoad } from "./tabs";

const STATUSES = [
  "",
  "RECEIVED",
  "TRACKED",
  "AUTO_FIX",
  "NEEDS_APPROVAL",
  "APPROVED",
  "REJECTED",
  "FIXED",
  "DISMISSED",
] as const;

/** Feedback statuses clash with the global map (PO "RECEIVED" = success;
 * feedback RECEIVED = "GitHub mirror pending" = warning), so tone locally. */
function feedbackTone(status: string): BadgeTone {
  switch (status) {
    case "RECEIVED":
    case "NEEDS_APPROVAL":
      return "warning";
    case "TRACKED":
      return "info";
    case "AUTO_FIX":
      return "brass";
    case "APPROVED":
    case "FIXED":
      return "success";
    case "REJECTED":
      return "danger";
    default:
      return "neutral";
  }
}

const TIER_ICON: Record<AdminFeedback["reporter_tier"], string> = {
  ANON: "👤",
  CUSTOMER: "🛒",
  STAFF: "🧑‍🍳",
};

export function FeedbackTab() {
  const [status, setStatus] = useState("");
  const load = useCallback(
    () => adminApi<AdminFeedbackList>(`/admin/feedback?limit=100${status ? `&status=${status}` : ""}`),
    [status],
  );
  const { data, error, refresh } = useLoad(load);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [triaging, setTriaging] = useState(false);

  const triageNow = async () => {
    setTriaging(true);
    try {
      await adminApi("/admin/feedback/triage-now", { method: "POST" });
      refresh();
    } finally {
      setTriaging(false);
    }
  };

  const decideWeb = async (id: number, action: "approve" | "reject") => {
    await adminApi(`/admin/feedback/${id}/decision`, { method: "POST", body: { action } });
    refresh();
  };

  return (
    <div className="space-y-4">
      <ErrorBar msg={error} />
      <div className="flex flex-wrap items-center gap-2">
        <Eyebrow>User reports → GitHub issues → AI fixer</Eyebrow>
        <div className="grow" />
        <Select value={status} onChange={(e) => setStatus(e.target.value)} className="w-44">
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s === "" ? "All statuses" : s}
            </option>
          ))}
        </Select>
        <Btn variant="magenta" size="sm" onClick={triageNow} disabled={triaging}>
          {triaging ? "Triaging…" : "🤖 Triage now"}
        </Btn>
        <Btn variant="ghost" size="sm" onClick={refresh}>
          ↻
        </Btn>
      </div>

      {data && data.reports.length === 0 && (
        <EmptyState>No feedback reports{status ? ` in ${status}` : " yet"}.</EmptyState>
      )}

      {data && data.reports.length > 0 && (
        <table className={tableCls}>
          <thead className={theadCls}>
            <tr>
              <th className={thCls}>#</th>
              <th className={thCls}>Type</th>
              <th className={thCls}>Status</th>
              <th className={thCls}>Title</th>
              <th className={thCls}>Reporter</th>
              <th className={thCls}>Issue</th>
              <th className={thCls}>Triage</th>
            </tr>
          </thead>
          <tbody>
            {data.reports.map((r) => (
              <Fragment key={r.id}>
                <tr
                  className={`${trCls} cursor-pointer`}
                  onClick={() => setExpanded(expanded === r.id ? null : r.id)}
                >
                  <td className={`${tdCls} tnum`}>{r.id}</td>
                  <td className={tdCls}>{r.type === "BUG" ? "🐞 Bug" : "✨ Feature"}</td>
                  <td className={tdCls}>
                    <Badge tone={feedbackTone(r.status)} surface="dark">
                      {r.status}
                    </Badge>
                  </td>
                  <td className={`${tdCls} max-w-[22rem] truncate`} title={r.title}>
                    {r.title}
                  </td>
                  <td className={tdCls}>
                    {TIER_ICON[r.reporter_tier]} {r.reporter_tier.toLowerCase()}
                  </td>
                  <td className={tdCls}>
                    {r.github_issue_number && data.github_repo ? (
                      <a
                        href={`https://github.com/${data.github_repo}/issues/${r.github_issue_number}`}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="font-semibold text-turmeric-400 underline decoration-dotted hover:text-turmeric-500"
                      >
                        #{r.github_issue_number} ↗
                      </a>
                    ) : r.github_error ? (
                      <span title={r.github_error}>⚠️ not mirrored</span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className={tdCls}>
                    {r.triage ? (
                      <span className="ai-meta">
                        🤖 {r.triage.verdict ?? "?"} · {r.triage.effort ?? "?"}/{r.triage.risk ?? "?"}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
                {expanded === r.id && (
                  <tr className={trCls}>
                    <td className={tdCls} colSpan={7}>
                      <div className="space-y-2 rounded-lg bg-indigo-800 p-3 text-xs">
                        <p className="whitespace-pre-wrap">{r.description}</p>
                        <p className="text-indigo-300">
                          {r.context?.route && <>route {r.context.route} · </>}
                          raised {new Date(r.created_at).toLocaleString("en-IN")}
                          {r.github_error && <> · GitHub: {r.github_error}</>}
                        </p>
                        {r.triage?.model && (
                          <span className="ai-meta">
                            🤖 {r.triage.model} · {r.triage.prompt_version}
                          </span>
                        )}
                        {r.status === "NEEDS_APPROVAL" && (
                          <div className="flex gap-2 pt-1">
                            <Btn
                              variant="veg"
                              size="sm"
                              onClick={() => decideWeb(r.id, "approve")}
                            >
                              ✅ Approve fixer run
                            </Btn>
                            <Btn
                              variant="danger"
                              size="sm"
                              onClick={() => decideWeb(r.id, "reject")}
                            >
                              🚫 Reject
                            </Btn>
                          </div>
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
      {data && (
        <p className="text-xs text-indigo-300">
          {data.total} report{data.total === 1 ? "" : "s"}
          {data.github_repo
            ? ` · mirrored to ${data.github_repo}`
            : " · GitHub integration disabled (API_GITHUB_TOKEN/REPO unset)"}
        </p>
      )}
    </div>
  );
}
