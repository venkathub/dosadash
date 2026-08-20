"use client";

/** Customer support chat (Phase 6): status / cancel / refund-request help.
 *  The support agent decides; the api executes under the real rules —
 *  refunds only ever become escalations for a human. */

import { useState } from "react";
import { api } from "../../lib/api";
import { Btn, Input } from "../components/ui";

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
        className="btn-gold fixed bottom-4 right-4 rounded-full px-4 py-2 text-sm font-bold shadow-lift transition-transform duration-200 hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brass-500"
        onClick={() => setOpen(true)}
      >
        🛟 Need help?
      </button>
    );

  return (
    <div className="fixed bottom-4 right-4 flex h-96 w-80 flex-col overflow-hidden rounded-2xl bg-cream-50 shadow-modal">
      <div className="flex items-center justify-between bg-leaf-800 px-3 py-2 text-sm">
        <b className="font-display font-semibold tracking-tight text-brass-300">🛟 Order help</b>
        <button
          className="text-leaf-200 transition-colors duration-150 hover:text-brass-300"
          onClick={() => setOpen(false)}
        >
          ✕
        </button>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3 text-sm">
        {history.length === 0 && (
          <p className="text-ink-400">
            Ask about an order — status, cancelling, or a refund request. E.g. “where is my order?”
          </p>
        )}
        {history.map((m, i) => (
          <p
            key={i}
            className={`animate-fade-up whitespace-pre-wrap px-3 py-2 ${
              m.role === "user"
                ? "ml-10 rounded-2xl rounded-br-md bg-leaf-700 text-cream-50"
                : "mr-10 rounded-2xl rounded-bl-md bg-cream-200 text-ink-900"
            }`}
          >
            {m.content}
          </p>
        ))}
        {busy && (
          <p className="mr-10 rounded-2xl rounded-bl-md bg-cream-200 px-3 py-2 text-ink-400">…</p>
        )}
      </div>
      <div className="flex gap-2 border-t border-cream-300 p-2">
        <Input
          tone="light"
          className="flex-1 rounded-full"
          value={input}
          placeholder="Type your question…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <Btn size="sm" disabled={busy} onClick={send}>
          Send
        </Btn>
      </div>
    </div>
  );
}
