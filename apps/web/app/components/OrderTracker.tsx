"use client";

import { useEffect, useRef, useState } from "react";
import { api, getToken, wsUrl, type Order } from "../../lib/api";

const STEPS = ["PLACED", "CONFIRMED", "COOKING", "READY", "OUT_FOR_DELIVERY", "DELIVERED"];

export default function OrderTracker({ order, onClose }: { order: Order; onClose: () => void }) {
  const [status, setStatus] = useState(order.status);
  const [paid, setPaid] = useState(order.payment?.status === "CAPTURED");
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const ws = new WebSocket(wsUrl(`/ws/orders/${order.id}`, token));
    wsRef.current = ws;
    ws.onmessage = (msg) => {
      const data = JSON.parse(msg.data);
      if (data.order_id === order.id) setStatus(data.status);
    };
    return () => ws.close(1000);
  }, [order.id]);

  const payDemo = async () => {
    setError(null);
    try {
      await api<Order>(`/orders/${order.id}/pay/demo`, { method: "POST", auth: true });
      setPaid(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "payment failed");
    }
  };

  const stepIdx = STEPS.indexOf(status);
  const cancelled = status === "CANCELLED" || status === "REFUNDED";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="w-96 space-y-4 rounded-xl bg-white p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold">Order #{order.id}</h2>
          <span className="text-sm text-stone-500">live ●</span>
        </div>
        <ul className="text-sm text-stone-600">
          {order.items.map((i) => (
            <li key={i.item_id}>
              {i.qty}× {i.name}
            </li>
          ))}
        </ul>
        <p className="text-sm">
          ₹{order.subtotal} + GST ₹{order.gst} = <b>₹{order.total}</b>
        </p>
        {!paid ? (
          <button className="w-full rounded bg-green-600 py-2 font-semibold text-white" onClick={payDemo}>
            💳 Pay ₹{order.total} (demo)
          </button>
        ) : (
          <p className="rounded bg-green-100 px-3 py-1 text-sm text-green-800">✓ Payment captured</p>
        )}
        {cancelled ? (
          <p className="rounded bg-red-100 px-3 py-2 text-sm text-red-700">Order {status.toLowerCase()}</p>
        ) : (
          <ol className="space-y-1">
            {STEPS.map((s, i) => (
              <li key={s} className={`flex items-center gap-2 text-sm ${i <= stepIdx ? "text-stone-900" : "text-stone-400"}`}>
                <span>{i < stepIdx ? "✅" : i === stepIdx ? "🟡" : "⚪"}</span>
                {s.replace(/_/g, " ")}
              </li>
            ))}
          </ol>
        )}
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button className="w-full rounded border border-stone-300 py-2 text-sm" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}
