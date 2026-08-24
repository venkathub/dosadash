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
        theme: { color: "#1B1B3A" },
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
        <SectionHeading as="h2" className="text-lg text-ink">
          Order #{order.id}
        </SectionHeading>
        <span className="text-sm text-muted">
          live <span className="animate-pulse-soft text-veg">●</span>
        </span>
      </div>
      <ul className="text-sm text-muted">
        {order.items.map((i) => (
          <li key={i.item_id}>
            {i.qty}× {i.name}
          </li>
        ))}
      </ul>
      <p className="tnum text-sm text-ink">
        ₹{order.subtotal}
        {order.discount && parseFloat(order.discount) > 0 ? (
          <span className="text-veg"> − ₹{order.discount}{order.coupon_code ? ` (${order.coupon_code})` : ""}</span>
        ) : null}{" "}
        + GST ₹{order.gst} = <b className="font-display">₹{order.total}</b>
      </p>
      {!paid ? (
        <Btn variant="magenta" className="w-full" onClick={pay}>
          {payLabel}
        </Btn>
      ) : (
        <p className="rounded-lg border-[1.5px] border-veg bg-veg-100 px-3 py-1 text-sm font-semibold text-veg">
          ✓ Payment captured
        </p>
      )}
      {cancelled ? (
        <p className="rounded-lg border-[1.5px] border-chili bg-chili-100 px-3 py-2 text-sm font-semibold text-chili">
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
                  ? "text-ink"
                  : i === stepIdx
                    ? "font-display font-bold text-ink"
                    : "text-faint",
              )}
            >
              <span
                className={cx(
                  "inline-flex h-5 w-5 items-center justify-center rounded-full border-2 text-[10px] font-bold",
                  i < stepIdx
                    ? "border-veg bg-veg text-white"
                    : i === stepIdx
                      ? "animate-pulse-soft border-indigo-900 bg-turmeric-500 text-indigo-900"
                      : "border-sand-300 bg-offwhite text-faint",
                )}
              >
                {i < stepIdx ? "✓" : "●"}
              </span>
              {s.replace(/_/g, " ")}
            </li>
          ))}
        </ol>
      )}
      {error && <p className="text-sm font-semibold text-chili">{error}</p>}
      <Btn variant="paper" className="w-full" onClick={onClose}>
        Close
      </Btn>
    </Modal>
  );
}
