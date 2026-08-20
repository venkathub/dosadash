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

  const payRazorpay = async () => {
    setError(null);
    try {
      const cfg = await api<{ provider: string; key_id: string | null }>("/payments/config");
      await new Promise<void>((resolve, reject) => {
        if (document.getElementById("rzp-js")) return resolve();
        const s = document.createElement("script");
        s.id = "rzp-js";
        s.src = "https://checkout.razorpay.com/v1/checkout.js";
        s.onload = () => resolve();
        s.onerror = () => reject(new Error("Failed to load Razorpay"));
        document.body.appendChild(s);
      });
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const Razorpay = (window as any).Razorpay;
      const rzp = new Razorpay({
        key: cfg.key_id,
        order_id: order.payment?.provider_order_id,
        name: "DosaDash",
        description: `Order #${order.id}`,
        theme: { color: "#f59e0b" },
        handler: async (resp: { razorpay_payment_id: string; razorpay_signature: string }) => {
          try {
            await api<Order>(`/orders/${order.id}/pay`, {
              method: "POST",
              auth: true,
              body: { payment_id: resp.razorpay_payment_id, signature: resp.razorpay_signature },
            });
            setError(null); // clear any error from earlier failed attempts
            setPaid(true);
          } catch (e) {
            setError(e instanceof Error ? e.message : "verification failed");
          }
        },
      });
      rzp.on("payment.failed", (r: { error: { description: string } }) =>
        setError(r.error.description)
      );
      rzp.open();
    } catch (e) {
      setError(e instanceof Error ? e.message : "payment failed");
    }
  };

  const pay = order.payment?.provider === "razorpay" ? payRazorpay : payDemo;
  const payLabel =
    order.payment?.provider === "razorpay" ? `💳 Pay ₹${order.total} (Razorpay test)` : `💳 Pay ₹${order.total} (demo)`;

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
          ₹{order.subtotal}
          {order.discount && parseFloat(order.discount) > 0 ? (
            <span className="text-green-700"> − ₹{order.discount}{order.coupon_code ? ` (${order.coupon_code})` : ""}</span>
          ) : null}{" "}
          + GST ₹{order.gst} = <b>₹{order.total}</b>
        </p>
        {!paid ? (
          <button className="w-full rounded bg-green-600 py-2 font-semibold text-white" onClick={pay}>
            {payLabel}
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
