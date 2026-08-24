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
        className="fixed bottom-20 right-4 z-50 flex h-14 w-14 items-center justify-center rounded-full border-2 border-indigo-900 bg-turmeric-500 text-2xl shadow-pop transition-transform duration-200 hover:-translate-y-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500"
        onClick={() => setOpen(true)}
      >
        🥞
      </button>
    );

  return (
    <div className="fixed bottom-4 right-4 z-50 flex h-[70vh] w-[min(24rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border-2 border-indigo-900 bg-sand-200 shadow-pop">
      <header className="flex items-center justify-between border-b-[3px] border-turmeric-500 bg-indigo-900 px-3 py-2">
        <span className="flex items-baseline gap-2">
          <b className="font-display font-bold tracking-tight text-white">🥞 Dosa Genie</b>
          <span className="text-[10px] font-semibold text-indigo-200">
            <span className="animate-pulse-soft text-veg">●</span> online
          </span>
        </span>
        <button
          aria-label="Close chat"
          className="text-indigo-200 transition-colors duration-150 hover:text-turmeric-400"
          onClick={() => setOpen(false)}
        >
          ✕
        </button>
      </header>

      <div ref={scrollRef} className="flex-1 space-y-2 overflow-y-auto p-3 text-sm">
        <p className="mx-auto w-fit rounded-full border-[1.5px] border-sand-300 bg-paper px-3 py-0.5 text-[11px] text-muted">
          🔒 phone numbers are redacted before the model sees them
        </p>
        {messages.length === 0 && (
          <p className="text-faint">
            Ask me anything — &ldquo;2 masala dosas and a filter coffee&rdquo;, &ldquo;which dosas are
            vegan?&rdquo;, &ldquo;is rava dosa gluten free?&rdquo;
          </p>
        )}
        {messages.map((m, i) => (
          <p
            key={i}
            className={
              m.role === "user"
                ? "animate-fade-up ml-10 rounded-xl rounded-br-[4px] border-2 border-indigo-900 bg-indigo-900 px-3 py-2 text-right text-indigo-100 shadow-[3px_3px_0_#C21F58]"
                : "animate-fade-up mr-10 whitespace-pre-wrap rounded-xl rounded-bl-[4px] border-2 border-indigo-900 bg-offwhite px-3 py-2 text-ink shadow-pop-sm"
            }
          >
            {m.content || "…"}
          </p>
        ))}
      </div>

      {(draft.items.length > 0 || warnings.length > 0) && (
        <div className="mx-2 mb-2 overflow-hidden rounded-lg border-2 border-indigo-900 bg-paper text-sm">
          <div className="border-b-2 border-indigo-900 bg-turmeric-500 px-3 py-1 font-display text-[11.5px] font-bold uppercase tracking-[0.1em] text-indigo-900">
            Order draft
          </div>
          <div className="px-3 py-2">
            {draft.items.map((i) => (
              <p key={i.item_id} className="flex justify-between py-0.5 text-ink">
                <span>
                  {i.qty}× {i.name}
                  {i.notes ? <span className="text-xs text-muted"> ({i.notes})</span> : null}
                </span>
                <span className="tnum font-display font-bold">
                  ₹{(parseFloat(i.unit_price) * i.qty).toFixed(0)}
                </span>
              </p>
            ))}
            {draft.items.length > 0 && (
              <p className="mt-1 flex items-baseline justify-between border-t-2 border-dashed border-sand-300 pt-1.5 text-ink">
                <span className="text-xs text-muted">Subtotal</span>
                <span className="tnum font-display text-lg font-bold">
                  ₹{parseFloat(draft.subtotal).toFixed(2)}{" "}
                  <span className="font-sans text-xs font-normal text-faint">+ GST</span>
                </span>
              </p>
            )}
            {warnings.map((w, i) => (
              <p
                key={i}
                className="mt-1 rounded-lg border-[1.5px] border-turmeric-600 bg-warn-100 px-2 py-1 text-xs font-semibold text-[#8A6A03]"
              >
                ⏰ {w}
              </p>
            ))}
            {draft.items.length > 0 && (
              <Btn
                variant="magenta"
                className="mb-1 mt-2 w-full"
                disabled={placing || busy}
                onClick={placeOrder}
              >
                {placing ? "Placing…" : ready ? "Place order →" : "Place this order"}
              </Btn>
            )}
          </div>
        </div>
      )}

      <form
        className="flex gap-2 border-t-[3px] border-turmeric-500 bg-indigo-900 p-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        <Input
          tone="dark"
          className="flex-1 rounded-full"
          placeholder={busy ? "Thinking…" : "Type your order or question…"}
          value={input}
          disabled={busy}
          onChange={(e) => setInput(e.target.value)}
        />
        <Btn variant="turmeric" size="sm" className="rounded-lg px-4" disabled={busy || !input.trim()}>
          ➤
        </Btn>
      </form>
    </div>
  );
}
