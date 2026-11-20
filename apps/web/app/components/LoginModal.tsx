"use client";

import { useState } from "react";
import { ApiError, api, saveSession, type User } from "../../lib/api";
import { Field, Modal, ModalHeader, popupField, popupPrimary } from "./ui";

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
  const [requested, setRequested] = useState(false);
  const [demoOtp, setDemoOtp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const requestOtp = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ demo_otp: string | null; channel: string }>("/auth/otp/request", {
        method: "POST",
        body: { phone },
      });
      setDemoOtp(r.demo_otp);
      setRequested(true);
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
    <Modal onClose={onClose} className="w-full max-w-sm space-y-5 p-6 sm:p-7">
      <ModalHeader eyebrow="Welcome" title="Log in to order" onClose={onClose}>
        {requested ? "Enter the 6-digit code to continue." : "We send a one-time code. No password to remember."}
      </ModalHeader>
      <Field label="Phone number" htmlFor="login-phone">
        <input
          id="login-phone"
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          className={`${popupField} h-12`}
          placeholder="98765 43210"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
      </Field>
      {!requested ? (
        <button className={popupPrimary} disabled={busy || phone.length < 10} onClick={requestOtp}>
          {busy ? "Sending…" : "Send code"}
        </button>
      ) : (
        <>
          {demoOtp !== null ? (
            <p className="rounded-2xl border border-ghee/40 bg-ghee/10 px-3.5 py-2.5 text-sm text-cream" role="status">
              Demo mode: your code is <b className="tnum font-display text-base text-ghee">{demoOtp}</b>
            </p>
          ) : (
            <p className="rounded-2xl border border-sky/40 bg-sky/10 px-3.5 py-2.5 text-sm text-cream" role="status">
              Code sent to your linked <b>Telegram</b>. Check your messages.
            </p>
          )}
          <Field label="One-time code" htmlFor="login-otp">
            <input
              id="login-otp"
              inputMode="numeric"
              autoComplete="one-time-code"
              maxLength={6}
              className={`${popupField} tnum h-12 text-center font-display text-xl tracking-[0.4em]`}
              placeholder="••••••"
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
            />
          </Field>
          <button className={popupPrimary} disabled={busy || otp.length !== 6} onClick={verify}>
            {busy ? "Checking…" : "Verify and continue"}
          </button>
        </>
      )}
      {error && (
        <p className="rounded-2xl border border-chili/50 bg-chili/10 px-3.5 py-2.5 text-sm font-semibold text-[#FF8B8B]" role="alert">
          {error}
        </p>
      )}
    </Modal>
  );
}
