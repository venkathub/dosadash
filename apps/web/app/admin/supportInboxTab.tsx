"use client";

/** Support escalation inbox (Phase 6): tickets the support agent must not
 *  resolve itself — refund requests and complaints. Resolve-with-refund runs
 *  the real provider refund through order_service (audited). */

import { useCallback, useState } from "react";
import { AdminApiError, adminApi } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

const inputCls =
  "rounded bg-stone-700 px-2 py-1 text-sm text-stone-100 placeholder-stone-400 outline-none focus:ring-1 focus:ring-amber-400";
const btnCls =
  "rounded bg-amber-500 px-2 py-1 text-xs font-semibold text-stone-900 hover:bg-amber-400 disabled:opacity-40";
const ghostBtnCls =
  "rounded border border-stone-600 px-2 py-1 text-xs text-stone-300 hover:border-amber-400 hover:text-amber-300";

type Escalation = {
  id: number;
  user_id: number;
  order_id: number | null;
  kind: string;
  status: string;
  customer_message: string;
  agent_summary: string | null;
  resolved_by: number | null;
  resolution_note: string | null;
  created_at: string;
};

export function SupportInboxTab() {
  const [status, setStatus] = useState("OPEN");
  const loadInbox = useCallback(
    () => adminApi<Escalation[]>(`/admin/escalations${status ? `?status=${status}` : ""}`),
    [status],
  );
  const { data: tickets, error, refresh, setError } = useLoad(loadInbox);

  const decide = async (id: number, action: "resolve" | "dismiss", refund: boolean) => {
    const note = window.prompt(refund ? "Refund note (required)?" : "Resolution note?");
    if (!note) return;
    try {
      await adminApi(`/admin/escalations/${id}/${action}`, { method: "POST", body: { note, refund } });
      refresh();
    } catch (e) {
      setError(e instanceof AdminApiError ? e.message : "action failed");
    }
  };

  return (
    <div>
      <ErrorBar msg={error} />
      <div className="mb-3 flex items-center gap-2">
        <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all</option>
          {["OPEN", "RESOLVED", "DISMISSED"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
        <button className={ghostBtnCls} onClick={refresh}>↻ refresh</button>
      </div>
      <div className="space-y-2">
        {(tickets ?? []).map((t) => (
          <div key={t.id} className="rounded bg-stone-800 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-amber-300">🎫 #{t.id}</span>
              <span className={`rounded px-2 py-0.5 text-xs ${t.kind === "refund" ? "bg-red-900/60 text-red-200" : "bg-stone-700"}`}>
                {t.kind}
              </span>
              <span className="rounded bg-stone-700 px-2 py-0.5 text-xs">{t.status}</span>
              <span className="text-xs text-stone-400">
                user {t.user_id}
                {t.order_id && ` · order #${t.order_id}`} · {new Date(t.created_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}
              </span>
              {t.status === "OPEN" && (
                <span className="ml-auto flex gap-2">
                  {t.kind === "refund" && t.order_id && (
                    <button className={`${btnCls} bg-red-400`} onClick={() => decide(t.id, "resolve", true)}>
                      💸 resolve + refund
                    </button>
                  )}
                  <button className={btnCls} onClick={() => decide(t.id, "resolve", false)}>resolve</button>
                  <button className={ghostBtnCls} onClick={() => decide(t.id, "dismiss", false)}>dismiss</button>
                </span>
              )}
            </div>
            <p className="mt-2 text-stone-300">“{t.customer_message}”</p>
            {t.agent_summary && <p className="mt-1 text-xs text-stone-400">🤖 {t.agent_summary}</p>}
            {t.resolution_note && <p className="mt-1 text-xs text-emerald-300">✔ {t.resolution_note}</p>}
          </div>
        ))}
        {tickets?.length === 0 && <p className="text-sm text-stone-400">Inbox zero 🎉</p>}
      </div>
    </div>
  );
}
