"use client";

import { useState } from "react";
import { ApiError, api, saveSession, type User } from "../../lib/api";
import { Btn, Input, Modal, SectionHeading } from "./ui";

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
    <Modal tone="light" onClose={onClose} className="w-80 space-y-3 p-6">
      <SectionHeading as="h2" className="text-lg text-ink">
        🥞 Login to order
      </SectionHeading>
      <Input
        tone="light"
        className="w-full py-2"
        placeholder="Phone (e.g. 98765 43210)"
        value={phone}
        onChange={(e) => setPhone(e.target.value)}
      />
      {!requested ? (
        <Btn
          variant="magenta"
          className="w-full"
          disabled={busy || phone.length < 10}
          onClick={requestOtp}
        >
          Send OTP
        </Btn>
      ) : (
        <>
          {demoOtp !== null ? (
            <p className="rounded-lg border-2 border-indigo-900 bg-turmeric-100 px-3 py-2 text-sm text-ink">
              📟 Demo mode — your OTP is <b className="font-display">{demoOtp}</b>
            </p>
          ) : (
            <p className="rounded-lg border-[1.5px] border-sky bg-sky-100 px-3 py-2 text-sm text-ink">
              ✈️ OTP sent to your linked <b>Telegram</b> — check your DMs
            </p>
          )}
          <Input
            tone="light"
            className="w-full py-2"
            placeholder="Enter OTP"
            value={otp}
            onChange={(e) => setOtp(e.target.value)}
          />
          <Btn
            variant="magenta"
            className="w-full"
            disabled={busy || otp.length !== 6}
            onClick={verify}
          >
            Verify & continue
          </Btn>
        </>
      )}
      {error && <p className="text-sm font-semibold text-chili">{error}</p>}
    </Modal>
  );
}
