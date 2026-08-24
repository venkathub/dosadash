"use client";

/** Support escalation inbox (Phase 6): tickets the support agent must not
 *  resolve itself — refund requests and complaints. Resolve-with-refund runs
 *  the real provider refund through order_service (audited). */

import { useCallback, useState } from "react";
import { Badge, Btn, EmptyState, Select, statusBadgeTone } from "../components/ui";
import { AdminApiError, adminApi } from "./adminApi";
import { ErrorBar, useLoad } from "./tabs";

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
        <Select tone="dark" className="px-2 py-1" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">all</option>
          {["OPEN", "RESOLVED", "DISMISSED"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </Select>
        <Btn variant="ghost" size="sm" onClick={refresh}>↻ refresh</Btn>
      </div>
      <div className="space-y-2">
        {(tickets ?? []).map((t) => (
          <div key={t.id} className="rounded-lg bg-indigo-800 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-mono text-turmeric-400">🎫 #{t.id}</span>
              <Badge tone={t.kind === "refund" ? "danger" : "neutral"}>{t.kind}</Badge>
              <Badge tone={statusBadgeTone(t.status)}>{t.status}</Badge>
              <span className="text-xs text-indigo-200/70">
                user {t.user_id}
                {t.order_id && ` · order #${t.order_id}`} · {new Date(t.created_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}
              </span>
              {t.status === "OPEN" && (
                <span className="ml-auto flex gap-2">
                  {t.kind === "refund" && t.order_id && (
                    <Btn variant="danger" size="sm" onClick={() => decide(t.id, "resolve", true)}>
                      💸 resolve + refund
                    </Btn>
                  )}
                  <Btn variant="gold" size="sm" onClick={() => decide(t.id, "resolve", false)}>resolve</Btn>
                  <Btn variant="ghost" size="sm" onClick={() => decide(t.id, "dismiss", false)}>dismiss</Btn>
                </span>
              )}
            </div>
            <p className="mt-2 text-indigo-200">“{t.customer_message}”</p>
            {t.agent_summary && <p className="mt-1"><span className="ai-meta">🤖 {t.agent_summary}</span></p>}
            {t.resolution_note && <p className="mt-1 text-xs text-[#5BD69B]">✔ {t.resolution_note}</p>}
          </div>
        ))}
        {tickets?.length === 0 && <EmptyState>Inbox zero 🎉</EmptyState>}
      </div>
    </div>
  );
}
