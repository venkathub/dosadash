"use client";

import { useEffect, useState } from "react";
import { clearAdminToken, getAdminToken, saveAdminToken } from "./adminApi";
import { CopilotTab } from "./copilotTab";
import { CouponsTab } from "./couponsTab";
import { ImagesTab } from "./imagesTab";
import { InventoryTab } from "./inventoryTab";
import { CrmTab, ReportsTab } from "./reportsTabs";
import { SupportInboxTab } from "./supportInboxTab";
import { TranslationsTab } from "./translationsTab";
import { AuditTab, CombosTab, CostsTab, EvalsTab, MenuTab, NutritionTab, OrdersTab, SettingsTab } from "./tabs";

const TABS = ["Menu", "Orders", "Inventory", "Support", "Reports", "CRM", "Copilot", "Combos", "Coupons", "Nutrition", "Translations", "Images", "Evals", "Costs", "Settings", "Audit"] as const;
type Tab = (typeof TABS)[number];

export default function Admin() {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState<Tab>("Menu");

  useEffect(() => {
    setToken(getAdminToken());
    setReady(true);
  }, []);

  if (!ready) return null;
  if (!token) return <AdminLogin onLogin={setToken} />;

  return (
    <main className="min-h-screen bg-stone-900 text-stone-100">
      <header className="flex items-center gap-4 border-b border-stone-800 px-4 py-3">
        <h1 className="text-lg font-bold text-amber-400">🥞 DosaDash — Backoffice</h1>
        <nav className="flex gap-1">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded px-3 py-1 text-sm ${
                tab === t ? "bg-amber-500 font-semibold text-stone-900" : "text-stone-300 hover:bg-stone-800"
              }`}
            >
              {t}
            </button>
          ))}
        </nav>
        <button
          className="ml-auto text-xs text-stone-400 hover:text-stone-200"
          onClick={() => {
            clearAdminToken();
            setToken(null);
          }}
        >
          sign out
        </button>
      </header>
      <section className="p-4" key={tab}>
        {tab === "Menu" && <MenuTab />}
        {tab === "Orders" && <OrdersTab />}
        {tab === "Inventory" && <InventoryTab />}
        {tab === "Support" && <SupportInboxTab />}
        {tab === "Reports" && <ReportsTab />}
        {tab === "CRM" && <CrmTab />}
        {tab === "Copilot" && <CopilotTab />}
        {tab === "Combos" && <CombosTab />}
        {tab === "Coupons" && <CouponsTab />}
        {tab === "Nutrition" && <NutritionTab />}
        {tab === "Translations" && <TranslationsTab />}
        {tab === "Images" && <ImagesTab />}
        {tab === "Evals" && <EvalsTab />}
        {tab === "Costs" && <CostsTab />}
        {tab === "Settings" && <SettingsTab />}
        {tab === "Audit" && <AuditTab />}
      </section>
    </main>
  );
}

function AdminLogin({ onLogin }: { onLogin: (token: string) => void }) {
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [demoOtp, setDemoOtp] = useState<string | null>(null);
  const [stage, setStage] = useState<"phone" | "otp">("phone");
  const [error, setError] = useState("");

  const post = async (path: string, body: unknown) => {
    const resp = await fetch(`/api/v1${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data?.detail ?? `HTTP ${resp.status}`);
    return data;
  };

  const request = async () => {
    try {
      const r = await post("/auth/otp/request", { phone });
      setDemoOtp(r.demo_otp);
      setStage("otp");
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
    }
  };

  const verify = async () => {
    try {
      const r = await post("/auth/otp/verify", { phone, otp });
      if (r.user.role !== "admin" && r.user.role !== "owner") {
        setError(`This account has role '${r.user.role}' — the backoffice needs admin/owner.`);
        return;
      }
      saveAdminToken(r.access_token);
      onLogin(r.access_token);
    } catch (e) {
      setError(e instanceof Error ? e.message : "verify failed");
    }
  };

  return (
    <main className="flex min-h-screen items-center justify-center bg-stone-900">
      <div className="w-80 rounded-xl bg-stone-800 p-6 shadow-xl">
        <h1 className="mb-4 text-lg font-bold text-amber-400">🥞 Backoffice sign-in</h1>
        {stage === "phone" ? (
          <>
            <input
              className="mb-3 w-full rounded bg-stone-700 px-3 py-2 text-stone-100 outline-none focus:ring-1 focus:ring-amber-400"
              placeholder="Phone number"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && request()}
            />
            <button className="w-full rounded bg-amber-500 py-2 font-semibold text-stone-900 hover:bg-amber-400" onClick={request}>
              Send OTP
            </button>
          </>
        ) : (
          <>
            {demoOtp ? (
              <p className="mb-2 text-sm text-stone-300">📟 Demo mode — your OTP is <b className="text-amber-300">{demoOtp}</b></p>
            ) : (
              <p className="mb-2 text-sm text-stone-300">✈️ OTP sent to your linked Telegram</p>
            )}
            <input
              className="mb-3 w-full rounded bg-stone-700 px-3 py-2 text-stone-100 outline-none focus:ring-1 focus:ring-amber-400"
              placeholder="6-digit OTP"
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && otp.length === 6 && verify()}
            />
            <button
              className="w-full rounded bg-amber-500 py-2 font-semibold text-stone-900 hover:bg-amber-400 disabled:opacity-40"
              disabled={otp.length !== 6}
              onClick={verify}
            >
              Sign in
            </button>
          </>
        )}
        {error && <p className="mt-3 text-sm text-red-300">⚠ {error}</p>}
      </div>
    </main>
  );
}
