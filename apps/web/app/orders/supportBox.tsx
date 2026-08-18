"use client";

/** Customer support chat (Phase 6): status / cancel / refund-request help.
 *  The support agent decides; the api executes under the real rules —
 *  refunds only ever become escalations for a human. */

import { useState } from "react";
import { api } from "../../lib/api";

type SupportMsg = { role: "user" | "assistant"; content: string };
type SupportReply = {
  reply: string;
  action: string;
  order: { id: number; status: string } | null;
  escalation_id: number | null;
};

export function SupportBox() {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState<SupportMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  const send = async () => {
    const message = input.trim();
    if (!message || busy) return;
    setBusy(true);
    setInput("");
    const next: SupportMsg[] = [...history, { role: "user", content: message }];
    setHistory(next);
    try {
      const r = await api<SupportReply>("/support/chat", {
        method: "POST",
        auth: true,
        body: { message, history: next.slice(0, -1).slice(-10) },
      });
      let text = r.reply;
      if (r.order) text += `\n\n📦 Order #${r.order.id}: ${r.order.status}`;
      if (r.escalation_id) text += `\n\n🎫 Ticket #${r.escalation_id} created.`;
      setHistory([...next, { role: "assistant", content: text }]);
    } catch (e) {
      setHistory([
        ...next,
        { role: "assistant", content: e instanceof Error ? `⚠️ ${e.message}` : "⚠️ Support unavailable" },
      ]);
    } finally {
      setBusy(false);
    }
  };

  if (!open)
    return (
      <button
        className="fixed bottom-4 right-4 rounded-full bg-amber-500 px-4 py-2 text-sm font-bold text-white shadow-lg"
        onClick={() => setOpen(true)}
      >
        🛟 Need help?
      </button>
    );

  return (
    <div className="fixed bottom-4 right-4 flex h-96 w-80 flex-col rounded-xl border border-amber-300 bg-white shadow-xl">
      <div className="flex items-center justify-between rounded-t-xl bg-amber-500 px-3 py-2 text-sm font-bold text-white">
        🛟 Order help
        <button onClick={() => setOpen(false)}>✕</button>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3 text-sm">
        {history.length === 0 && (
          <p className="text-stone-500">
            Ask about an order — status, cancelling, or a refund request. E.g. “where is my order?”
          </p>
        )}
        {history.map((m, i) => (
          <p
            key={i}
            className={`whitespace-pre-wrap rounded-lg px-3 py-2 ${
              m.role === "user" ? "ml-8 bg-amber-100" : "mr-8 bg-stone-100"
            }`}
          >
            {m.content}
          </p>
        ))}
        {busy && <p className="mr-8 rounded-lg bg-stone-100 px-3 py-2 text-stone-400">…</p>}
      </div>
      <div className="flex gap-2 border-t border-stone-200 p-2">
        <input
          className="flex-1 rounded border border-stone-300 px-2 py-1 text-sm outline-none focus:border-amber-400"
          value={input}
          placeholder="Type your question…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button className="rounded bg-amber-500 px-3 py-1 text-sm font-bold text-white disabled:opacity-40" disabled={busy} onClick={send}>
          Send
        </button>
      </div>
    </div>
  );
}
