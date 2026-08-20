"use client";

import { useEffect, useState } from "react";
import {
  Badge,
  Btn,
  Card,
  Eyebrow,
  Input,
  SectionHeading,
} from "../components/ui";
import { clearAdminToken, getAdminToken, saveAdminToken } from "./adminApi";
import { CopilotTab } from "./copilotTab";
import { CouponsTab } from "./couponsTab";
import { ImagesTab } from "./imagesTab";
import { InventoryTab } from "./inventoryTab";
import { CrmTab, ReportsTab } from "./reportsTabs";
import { ReviewsTab } from "./reviewsTab";
import { SupportInboxTab } from "./supportInboxTab";
import { TranslationsTab } from "./translationsTab";
import { AuditTab, CombosTab, CostsTab, EvalsTab, MenuTab, NutritionTab, OrdersTab, SettingsTab } from "./tabs";

const TAB_GROUPS = [
  { label: "Operations", tabs: ["Menu", "Orders", "Inventory", "Support", "Reviews"] },
  { label: "Growth", tabs: ["Coupons", "Combos", "CRM", "Reports"] },
  { label: "AI Studio", tabs: ["Copilot", "Nutrition", "Translations", "Images", "Evals", "Costs"] },
  { label: "System", tabs: ["Settings", "Audit"] },
] as const;

const TABS = ["Menu", "Orders", "Inventory", "Support", "Reviews", "Coupons", "Combos", "CRM", "Reports", "Copilot", "Nutrition", "Translations", "Images", "Evals", "Costs", "Settings", "Audit"] as const;
type Tab = (typeof TABS)[number];

/** Display-only: read the role claim out of the stored JWT for the header chip. */
function roleFromToken(token: string): string | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return typeof payload.role === "string" ? payload.role : null;
  } catch {
    return null;
  }
}

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

  const role = roleFromToken(token);
  const activeGroup = TAB_GROUPS.find((g) => (g.tabs as readonly string[]).includes(tab));

  return (
    <main className="min-h-screen bg-leaf-950 text-leaf-100">
      <header className="border-b border-white/5 px-4 py-3">
        <div className="flex items-center gap-3">
          <h1 className="font-display text-lg font-semibold tracking-tight text-brass-300">🥞 DosaDash</h1>
          <Eyebrow>Backoffice</Eyebrow>
          {role && <Badge tone="brass">{role}</Badge>}
          <Btn
            variant="ghost"
            size="sm"
            className="ml-auto"
            onClick={() => {
              clearAdminToken();
              setToken(null);
            }}
          >
            sign out
          </Btn>
        </div>
        <nav className="mt-3 flex items-center gap-1 overflow-x-auto whitespace-nowrap pb-1">
          {TAB_GROUPS.map((group, gi) => (
            <span key={group.label} className="flex items-center gap-1">
              {gi > 0 && (
                <span aria-hidden className="mx-1.5 select-none tracking-[0.2em] text-brass-400/50">
                  ·∙·
                </span>
              )}
              <Eyebrow className="mr-1 hidden text-brass-300/50 sm:block">{group.label}</Eyebrow>
              {group.tabs.map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={
                    tab === t
                      ? "rounded-full btn-gold px-3 py-1 text-xs font-semibold"
                      : "rounded-full px-3 py-1 text-xs text-leaf-200 transition-colors duration-150 hover:bg-leaf-800 hover:text-brass-300"
                  }
                >
                  {t}
                </button>
              ))}
            </span>
          ))}
        </nav>
      </header>
      <section className="p-4" key={tab}>
        <div className="mb-3">
          {activeGroup && <Eyebrow>{activeGroup.label}</Eyebrow>}
          <SectionHeading as="h2" className="inline-block text-xl text-leaf-100">
            {tab}
          </SectionHeading>
        </div>
        <Card tone="dark" className="p-4 sm:p-5">
          {tab === "Menu" && <MenuTab />}
          {tab === "Orders" && <OrdersTab />}
          {tab === "Inventory" && <InventoryTab />}
          {tab === "Support" && <SupportInboxTab />}
          {tab === "Reviews" && <ReviewsTab />}
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
        </Card>
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
    <main className="flex min-h-screen items-center justify-center bg-leaf-950">
      <Card tone="dark" className="w-80 p-6 shadow-modal">
        <div className="mb-4">
          <div className="font-display text-lg font-semibold tracking-tight text-brass-300">🥞 DosaDash</div>
          <SectionHeading as="h1" className="text-xl text-leaf-100">
            Backoffice
          </SectionHeading>
        </div>
        {stage === "phone" ? (
          <>
            <Input
              tone="dark"
              className="mb-3 w-full py-2"
              placeholder="Phone number"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && request()}
            />
            <Btn variant="gold" className="w-full py-2" onClick={request}>
              Send OTP
            </Btn>
          </>
        ) : (
          <>
            {demoOtp ? (
              <p className="mb-2 text-sm text-leaf-200">📟 Demo mode — your OTP is <b className="text-brass-300">{demoOtp}</b></p>
            ) : (
              <p className="mb-2 text-sm text-leaf-200">✈️ OTP sent to your linked Telegram</p>
            )}
            <Input
              tone="dark"
              className="mb-3 w-full py-2"
              placeholder="6-digit OTP"
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && otp.length === 6 && verify()}
            />
            <Btn variant="gold" className="w-full py-2" disabled={otp.length !== 6} onClick={verify}>
              Sign in
            </Btn>
          </>
        )}
        {error && <p className="mt-3 text-sm text-chili-200">⚠ {error}</p>}
      </Card>
    </main>
  );
}
