"use client";

import { useState } from "react";
import { ApiError, api, saveSession, type User } from "../../lib/api";

type TokenResponse = { access_token: string; user: User };

export default function LoginModal({
  onClose,
  onLogin,
}: {
  onClose: () => void;
  onLogin: (user: User) => void;
}) {
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [demoOtp, setDemoOtp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const requestOtp = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ demo_otp: string | null }>("/auth/otp/request", {
        method: "POST",
        body: { phone },
      });
      setDemoOtp(r.demo_otp);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "OTP request failed");
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<TokenResponse>("/auth/otp/verify", {
        method: "POST",
        body: { phone, otp },
      });
      saveSession(r.access_token, r.user);
      onLogin(r.user);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Verification failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div className="w-80 space-y-3 rounded-xl bg-white p-6 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-bold">🥞 Login to order</h2>
        <input
          className="w-full rounded border border-stone-300 px-3 py-2"
          placeholder="Phone (e.g. 98765 43210)"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
        {demoOtp === null ? (
          <button
            className="w-full rounded bg-amber-500 py-2 font-semibold disabled:opacity-50"
            disabled={busy || phone.length < 10}
            onClick={requestOtp}
          >
            Send OTP
          </button>
        ) : (
          <>
            <p className="rounded bg-amber-100 px-3 py-2 text-sm">
              📟 Demo mode — your OTP is <b>{demoOtp}</b>
            </p>
            <input
              className="w-full rounded border border-stone-300 px-3 py-2"
              placeholder="Enter OTP"
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
            />
            <button
              className="w-full rounded bg-amber-500 py-2 font-semibold disabled:opacity-50"
              disabled={busy || otp.length !== 6}
              onClick={verify}
            >
              Verify & continue
            </button>
          </>
        )}
        {error && <p className="text-sm text-red-600">{error}</p>}
      </div>
    </div>
  );
}
