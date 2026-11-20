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
        className="fixed bottom-4 right-4 rounded-full border border-cream/20 bg-soot-900 px-4 py-2.5 font-display text-sm font-semibold text-cream shadow-noir transition-colors duration-150 hover:border-ghee focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ghee"
        onClick={() => setOpen(true)}
      >
        🛟 Need help?
      </button>
    );

  return (
    <div className="fixed bottom-4 right-4 z-50 flex h-96 w-[min(20rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-3xl border border-cream/15 bg-soot-900 shadow-noir">
      <div className="flex items-center justify-between border-b-[3px] border-ghee/50 bg-soot-900 px-3 py-2 text-sm">
        <b className="font-display font-bold tracking-tight text-cream">🛟 Order help</b>
        <button
          className="text-cream-dim transition-colors duration-150 hover:text-ghee"
          onClick={() => setOpen(false)}
        >
          ✕
        </button>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3 text-sm">
        {history.length === 0 && (
          <p className="text-cream-faint">
            Ask about an order — status, cancelling, or a refund request. E.g. “where is my order?”
          </p>
        )}
        {history.map((m, i) => (
          <p
            key={i}
            className={`animate-fade-up whitespace-pre-wrap border-2 border-cream/15 px-3 py-2 ${
              m.role === "user"
                ? "ml-10 rounded-xl rounded-br-[4px] bg-soot-900 text-cream"
                : "mr-10 rounded-xl rounded-bl-[4px] bg-soot-950 text-cream"
            }`}
          >
            {m.content}
          </p>
        ))}
        {busy && (
          <p className="mr-10 rounded-xl rounded-bl-[4px] border-2 border-cream/15 bg-soot-950 px-3 py-2 text-cream-faint">
            …
          </p>
        )}
      </div>
      <div className="flex gap-2 border-t-[3px] border-ghee/50 bg-soot-900 p-2">
        <Input
          tone="dark"
          className="flex-1 rounded-full"
          value={input}
          placeholder="Type your question…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <Btn variant="turmeric" size="sm" disabled={busy} onClick={send}>
          Send
        </Btn>
      </div>
    </div>
  );
}
