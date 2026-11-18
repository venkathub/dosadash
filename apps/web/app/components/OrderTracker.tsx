"use client";

import { useEffect, useState } from "react";
import { api, expireSession, getToken, type Order } from "../../lib/api";
import { openLiveSocket } from "../../lib/liveSocket";
import { rupee } from "../../lib/format";
import { Modal, ModalHeader, cx, popupPrimary } from "./ui";

// Server status → what the customer reads. Keys are the order state machine's.
const STEPS = [
  { key: "PLACED", label: "Order placed", note: "waiting for the kitchen" },
  { key: "CONFIRMED", label: "Kitchen confirmed", note: "queued to cook" },
  { key: "COOKING", label: "On the tawa", note: "being cooked now" },
  { key: "READY", label: "Ready", note: "plated and packed" },
  { key: "OUT_FOR_DELIVERY", label: "On the way", note: "with the rider" },
  { key: "DELIVERED", label: "Delivered", note: "enjoy your meal" },
];

export default function OrderTracker({ order, onClose }: { order: Order; onClose: () => void }) {
  const [status, setStatus] = useState(order.status);
  const [paid, setPaid] = useState(order.payment?.status === "CAPTURED");
  const [error, setError] = useState<string | null>(null);

  // Auto-reconnecting: a deploy/proxy blip used to freeze the tracker silently.
  // The server sends a fresh snapshot on every (re)connect, so no event is lost.
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    return openLiveSocket({
      path: `/ws/orders/${order.id}`,
      token,
      onAuthFailure: (reason) => {
        if (reason === "forbidden") return setError("Live updates unavailable for this order");
        expireSession();
        setError("Session expired — log in again to see live updates");
      },
      onMessage: (raw) => {
        const data = JSON.parse(raw);
        if (data.order_id === order.id) setStatus(data.status);
      },
    });
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
        theme: { color: "#14100C" },
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

  const razorpay = order.payment?.provider === "razorpay";
  const pay = razorpay ? payRazorpay : payDemo;
  const stepIdx = STEPS.findIndex((s) => s.key === status);
  const cancelled = status === "CANCELLED" || status === "REFUNDED";
  const delivered = status === "DELIVERED";
  const current = STEPS[stepIdx];
  const hasDiscount = !!order.discount && parseFloat(order.discount) > 0;

  return (
    <Modal onClose={onClose} className="w-full max-w-md space-y-5 p-6 sm:p-7">
      <ModalHeader
        eyebrow={cancelled ? "Order update" : delivered ? "Delivered" : "Live order"}
        title={<>Order <span className="tnum">#{order.id}</span></>}
        onClose={onClose}
      >
        {cancelled ? (
          <>This order was {status.toLowerCase()}.</>
        ) : (
          <>
            {!delivered && (
              <span aria-hidden="true" className="animate-pulse-soft mr-2 inline-block h-2 w-2 rounded-full bg-ember align-middle" />
            )}
            <span className="font-semibold text-cream">{current?.label ?? status}</span>
            {current && <> · {current.note}</>}
          </>
        )}
      </ModalHeader>

      {/* progress: finished steps are ticked, the live one glows, the rest wait */}
      {!cancelled && (
        <ol className="relative" aria-label="Order progress">
          <span aria-hidden="true" className="absolute bottom-4 left-[13px] top-4 w-0.5 bg-soot-700" />
          <span
            aria-hidden="true"
            className="absolute left-[13px] top-4 w-0.5 bg-basil transition-[height] duration-700"
            style={{ height: `calc((100% - 2rem) * ${Math.max(0, stepIdx) / (STEPS.length - 1)})` }}
          />
          {STEPS.map((s, i) => {
            const state = i < stepIdx || delivered ? "done" : i === stepIdx ? "live" : "todo";
            return (
              <li key={s.key} aria-current={state === "live" ? "step" : undefined} className="relative flex items-center gap-3.5 py-1.5">
                <span
                  className={cx(
                    "relative z-[1] grid h-7 w-7 shrink-0 place-items-center rounded-full border-2 font-display text-xs font-bold",
                    state === "done" && "border-basil bg-basil text-soot-950",
                    state === "live" && "border-ember bg-ember text-ember-ink shadow-ember",
                    state === "todo" && "border-soot-700 bg-soot-900 text-cream-faint",
                  )}
                >
                  {state === "done" ? "✓" : i + 1}
                </span>
                <span
                  className={cx(
                    "text-[15px]",
                    state === "live" ? "font-display font-semibold text-cream" : state === "done" ? "text-cream-dim" : "text-cream-faint",
                  )}
                >
                  {s.label}
                </span>
                {state === "live" && (
                  <span className="rounded-full border border-ember/50 bg-ember/10 px-2 py-0.5 text-xs font-semibold text-ember-hot">
                    now
                  </span>
                )}
              </li>
            );
          })}
        </ol>
      )}
      {cancelled && (
        <p className="rounded-2xl border border-chili/50 bg-chili/10 px-3.5 py-2.5 text-sm font-semibold text-[#FF8B8B]" role="status">
          {status === "REFUNDED" ? "Your payment has been refunded." : "Nothing more will be prepared for this order."}
        </p>
      )}

      {/* what was ordered, and the bill */}
      <div className="rounded-2xl border border-cream/10 bg-soot-950 p-4">
        <ul className="space-y-1.5 text-sm">
          {order.items.map((i) => (
            <li key={i.item_id} className="flex justify-between gap-3">
              <span className="text-cream">
                <span className="tnum text-cream-faint">{i.qty} ×</span> {i.name}
              </span>
              <span className="tnum text-cream-dim">{rupee(parseFloat(i.unit_price) * i.qty)}</span>
            </li>
          ))}
        </ul>
        <dl className="mt-3 space-y-1 border-t border-cream/10 pt-3 text-sm">
          <div className="flex justify-between text-cream-dim">
            <dt>Subtotal</dt>
            <dd className="tnum">{rupee(order.subtotal)}</dd>
          </div>
          {hasDiscount && (
            <div className="flex justify-between text-basil">
              <dt>Coupon{order.coupon_code ? ` ${order.coupon_code}` : ""}</dt>
              <dd className="tnum">−{rupee(order.discount!)}</dd>
            </div>
          )}
          <div className="flex justify-between text-cream-dim">
            <dt>GST</dt>
            <dd className="tnum">{rupee(order.gst)}</dd>
          </div>
          <div className="flex items-baseline justify-between pt-1.5 text-cream">
            <dt className="font-display font-semibold">Total</dt>
            <dd className="tnum font-display text-2xl font-semibold">{rupee(order.total)}</dd>
          </div>
        </dl>
      </div>

      {/* payment */}
      {paid ? (
        <p className="flex items-center gap-2 rounded-2xl border border-basil/40 bg-basil/10 px-3.5 py-2.5 text-sm font-semibold text-basil" role="status">
          <span aria-hidden="true">✓</span> Paid {rupee(order.total)}
        </p>
      ) : (
        !cancelled && (
          <div>
            <button className={popupPrimary} onClick={pay}>
              Pay {rupee(order.total)}
            </button>
            <p className="mt-2 text-center text-xs text-cream-faint">
              {razorpay ? "Razorpay test mode. No real money is charged." : "Demo payment. No real money is charged."}
            </p>
          </div>
        )
      )}
      {error && (
        <p className="rounded-2xl border border-chili/50 bg-chili/10 px-3.5 py-2.5 text-sm font-semibold text-[#FF8B8B]" role="alert">
          {error}
        </p>
      )}
    </Modal>
  );
}
