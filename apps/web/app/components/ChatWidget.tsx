"use client";

import { useEffect, useRef, useState } from "react";
import { getToken, getUser } from "../../lib/api";
import { Btn, Input } from "./ui";

type DraftItem = { item_id: number; name: string; qty: number; unit_price: string; notes: string | null };
type Draft = { items: DraftItem[]; subtotal: string };
type ChatMsg = { role: "user" | "assistant"; content: string };

type FinalData = {
  reply: string;
  draft: Draft;
  ready_to_place: boolean;
  warnings: string[];
  kitchen_open: boolean;
};

type Props = {
  onPlaceOrder: (items: { item_id: number; qty: number }[]) => Promise<void>;
  onRequireLogin: () => void;
};

const EMPTY_DRAFT: Draft = { items: [], subtotal: "0" };

export default function ChatWidget({ onPlaceOrder, onRequireLogin }: Props) {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [placing, setPlacing] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, open]);

  const send = async () => {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setBusy(true);
    setWarnings([]);
    const history = messages.slice(-24);
    setMessages((m) => [...m, { role: "user", content: message }, { role: "assistant", content: "" }]);

    const patchAssistant = (updater: (prev: string) => string) =>
      setMessages((m) => {
        const next = [...m];
        next[next.length - 1] = { role: "assistant", content: updater(next[next.length - 1].content) };
        return next;
      });

    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      const token = getToken();
      if (token) headers.Authorization = `Bearer ${token}`;
      const resp = await fetch("/api/v1/chat/stream", {
        method: "POST",
        headers,
        body: JSON.stringify({ message, history, draft: draft.items.length ? draft : null }),
      });
      if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let sawFinal = false;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          if (!frame.startsWith("data: ")) continue;
          const event = JSON.parse(frame.slice(6));
          if (event.type === "delta") patchAssistant((prev) => prev + event.text);
          else if (event.type === "final") {
            const data = event.data as FinalData;
            sawFinal = true;
            patchAssistant(() => data.reply);
            setDraft(data.draft);
            setWarnings(data.warnings);
            setReady(data.ready_to_place);
          } else if (event.type === "error") {
            patchAssistant(() => "Sorry, the assistant is unavailable right now — please try again.");
          }
        }
      }
      if (!sawFinal) patchAssistant((prev) => prev || "Sorry, something went wrong — please try again.");
    } catch {
      patchAssistant(() => "Sorry, the assistant is unavailable right now — please try again.");
    } finally {
      setBusy(false);
    }
  };

  const placeOrder = async () => {
    if (!getUser()) return onRequireLogin();
    setPlacing(true);
    try {
      await onPlaceOrder(draft.items.map((i) => ({ item_id: i.item_id, qty: i.qty })));
      setDraft(EMPTY_DRAFT);
      setReady(false);
      setMessages((m) => [...m, { role: "assistant", content: "Order placed! You can track it above. 🎉" }]);
    } catch (e) {
      setWarnings([e instanceof Error ? e.message : "Order failed — please try again."]);
    } finally {
      setPlacing(false);
    }
  };

  if (!open)
    return (
      <button
        aria-label="Chat with DosaDash"
        title="Ask Dosa Genie"
        className="btn-gold fixed bottom-20 right-4 z-50 flex h-14 w-14 items-center justify-center rounded-full text-2xl shadow-lift transition-transform duration-200 hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brass-500"
        onClick={() => setOpen(true)}
      >
        🥞
      </button>
    );

  return (
    <div className="fixed bottom-4 right-4 z-50 flex h-[70vh] w-[min(24rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl bg-cream-50 shadow-modal">
      <header className="flex items-center justify-between bg-leaf-800 px-3 py-2">
        <span className="flex items-baseline gap-2">
          <b className="font-display font-semibold tracking-tight text-brass-300">🥞 Dosa Genie</b>
          <span className="text-[10px] font-semibold text-veg-500">
            <span className="animate-pulse-soft">●</span> online
          </span>
        </span>
        <button
          aria-label="Close chat"
          className="text-leaf-200 transition-colors duration-150 hover:text-brass-300"
          onClick={() => setOpen(false)}
        >
          ✕
        </button>
      </header>

      <div ref={scrollRef} className="flex-1 space-y-2 overflow-y-auto p-3 text-sm">
        {messages.length === 0 && (
          <p className="text-ink-400">
            Ask me anything — &ldquo;2 masala dosas and a filter coffee&rdquo;, &ldquo;which dosas are
            vegan?&rdquo;, &ldquo;is rava dosa gluten free?&rdquo;
          </p>
        )}
        {messages.map((m, i) => (
          <p
            key={i}
            className={
              m.role === "user"
                ? "animate-fade-up ml-10 rounded-2xl rounded-br-md bg-leaf-700 px-3 py-2 text-right text-cream-50"
                : "animate-fade-up mr-10 whitespace-pre-wrap rounded-2xl rounded-bl-md bg-cream-200 px-3 py-2 text-ink-900"
            }
          >
            {m.content || "…"}
          </p>
        ))}
      </div>

      {(draft.items.length > 0 || warnings.length > 0) && (
        <div className="mx-2 mb-2 rounded-xl border border-brass-500/40 bg-cream-50 px-3 py-2 text-sm shadow-card">
          {draft.items.map((i) => (
            <p key={i.item_id} className="flex justify-between text-ink-900">
              <span>
                {i.qty}× {i.name}
                {i.notes ? <span className="text-xs text-ink-600"> ({i.notes})</span> : null}
              </span>
              <span className="tnum">₹{(parseFloat(i.unit_price) * i.qty).toFixed(0)}</span>
            </p>
          ))}
          {draft.items.length > 0 && (
            <p className="mt-1 flex justify-between border-t border-dashed border-cream-300 pt-1 font-bold text-ink-900">
              <span>Subtotal</span>
              <span className="tnum font-display">
                ₹{parseFloat(draft.subtotal).toFixed(2)}{" "}
                <span className="font-sans font-normal text-ink-400">+ GST</span>
              </span>
            </p>
          )}
          {warnings.map((w, i) => (
            <p
              key={i}
              className="mt-1 rounded-full border border-turmeric-500/40 bg-turmeric-200 px-2 py-0.5 text-xs text-ink-900"
            >
              ⚠ {w}
            </p>
          ))}
          {draft.items.length > 0 && (
            <Btn className="mt-2 w-full" disabled={placing || busy} onClick={placeOrder}>
              {placing ? "Placing…" : ready ? "✅ Place order" : "Place this order"}
            </Btn>
          )}
        </div>
      )}

      <form
        className="flex gap-2 border-t border-cream-300 p-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <Input
          tone="light"
          className="flex-1 rounded-full"
          placeholder={busy ? "Thinking…" : "Type your order or question…"}
          value={input}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
        />
        <Btn size="sm" className="rounded-full px-4" disabled={busy || !input.trim()}>
          ➤
        </Btn>
      </form>
    </div>
  );
}
