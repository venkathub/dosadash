"use client";

import { useEffect, useRef, useState } from "react";
import { api, getToken, wsUrl, type Order } from "../../lib/api";
import { Btn, Modal, SectionHeading, cx } from "./ui";

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
        theme: { color: "#14342B" },
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
    <Modal tone="light" onClose={onClose} className="w-96 space-y-4 p-6">
      <div className="flex items-center justify-between">
        <SectionHeading as="h2" className="text-lg text-leaf-800">
          Order #{order.id}
        </SectionHeading>
        <span className="text-sm text-ink-600">
          live <span className="animate-pulse-soft text-veg-500">●</span>
        </span>
      </div>
      <ul className="text-sm text-ink-600">
        {order.items.map((i) => (
          <li key={i.item_id}>
            {i.qty}× {i.name}
          </li>
        ))}
      </ul>
      <p className="tnum text-sm text-ink-900">
        ₹{order.subtotal}
        {order.discount && parseFloat(order.discount) > 0 ? (
          <span className="text-veg-600"> − ₹{order.discount}{order.coupon_code ? ` (${order.coupon_code})` : ""}</span>
        ) : null}{" "}
        + GST ₹{order.gst} = <b className="font-display">₹{order.total}</b>
      </p>
      {!paid ? (
        <Btn className="w-full" onClick={pay}>
          {payLabel}
        </Btn>
      ) : (
        <p className="rounded-lg border border-veg-500/30 bg-veg-200 px-3 py-1 text-sm text-veg-600">
          ✓ Payment captured
        </p>
      )}
      {cancelled ? (
        <p className="rounded-lg border border-chili-500/30 bg-chili-200 px-3 py-2 text-sm text-chili-600">
          Order {status.toLowerCase()}
        </p>
      ) : (
        <ol className="space-y-1.5">
          {STEPS.map((s, i) => (
            <li
              key={s}
              className={cx(
                "flex items-center gap-2 text-sm",
                i < stepIdx
                  ? "text-ink-900"
                  : i === stepIdx
                    ? "font-semibold text-ink-900"
                    : "text-ink-400",
              )}
            >
              <span
                className={cx(
                  "inline-flex h-5 w-5 items-center justify-center rounded-full border text-[10px] font-bold",
                  i < stepIdx
                    ? "border-veg-500 bg-veg-500 text-cream-50"
                    : i === stepIdx
                      ? "animate-pulse-soft border-brass-500 bg-brass-500 text-leaf-900"
                      : "border-cream-300 bg-cream-100 text-ink-400",
                )}
              >
                {i < stepIdx ? "✓" : "●"}
              </span>
              {s.replace(/_/g, " ")}
            </li>
          ))}
        </ol>
      )}
      {error && <p className="text-sm text-chili-600">{error}</p>}
      <button
        className="w-full rounded-lg border border-leaf-600 py-2 text-sm font-semibold text-leaf-800 transition-colors duration-150 hover:border-brass-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brass-500"
        onClick={onClose}
      >
        Close
      </button>
    </Modal>
  );
}
