"use client";

import { useEffect, useState } from "react";
import {
  Badge,
  Btn,
  Card,
  Eyebrow,
  Input,
  SectionHeading,
  cx,
} from "../components/ui";
import { clearAdminToken, getAdminToken, saveAdminToken } from "./adminApi";
import { CopilotTab } from "./copilotTab";
import { CouponsTab } from "./couponsTab";
import { FeedbackTab } from "./feedbackTab";
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
  { label: "System", tabs: ["Feedback", "Settings", "Audit"] },
] as const;

const TABS = ["Menu", "Orders", "Inventory", "Support", "Reviews", "Coupons", "Combos", "CRM", "Reports", "Copilot", "Nutrition", "Translations", "Images", "Evals", "Costs", "Feedback", "Settings", "Audit"] as const;
type Tab = (typeof TABS)[number];

/** Sidebar icons — the product's emoji icon language (docs/13 §4). */
const TAB_ICON: Record<Tab, string> = {
  Menu: "🍽️",
  Orders: "🧾",
  Inventory: "📦",
  Support: "🛟",
  Reviews: "⭐",
  Coupons: "🎟️",
  Combos: "🧩",
  CRM: "👥",
  Reports: "📈",
  Copilot: "🤖",
  Nutrition: "🥗",
  Translations: "🌐",
  Images: "🖼️",
  Evals: "🎯",
  Costs: "💸",
  Feedback: "🐞",
  Settings: "⚙️",
  Audit: "📜",
};

/** Display-only: read the role claim out of the stored JWT for the header chip. */
function roleFromToken(token: string): string | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1]));
    return typeof payload.role === "string" ? payload.role : null;
  } catch {
    return null;
  }
}

/** The one grouped vertical nav — desktop sidebar and mobile drawer both render this. */
function GroupedNav({ tab, onSelect }: { tab: Tab; onSelect: (t: Tab) => void }) {
  return (
    <nav aria-label="Backoffice sections">
      {TAB_GROUPS.map((group) => (
        <div key={group.label} className="mb-4">
          <div className="mb-1.5 px-2.5 font-display text-[10px] font-bold uppercase tracking-[0.16em] text-indigo-300">
            {group.label}
          </div>
          <div className="space-y-1">
            {group.tabs.map((t) => (
              <button
                key={t}
                onClick={() => onSelect(t)}
                aria-current={tab === t ? "page" : undefined}
                className={cx(
                  "flex w-full items-center gap-2 rounded-lg border-[1.5px] px-2.5 py-1.5 text-left font-display text-[13px] transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500",
                  tab === t
                    ? "border-turmeric-500 bg-turmeric-500 font-bold text-indigo-950"
                    : "border-transparent font-semibold text-indigo-200 hover:bg-indigo-800 hover:text-turmeric-400",
                )}
              >
                <span aria-hidden className="w-5 text-center">
                  {TAB_ICON[t]}
                </span>
                {t}
              </button>
            ))}
          </div>
        </div>
      ))}
    </nav>
  );
}

export default function Admin() {
  const [token, setToken] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState<Tab>("Menu");
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    setToken(getAdminToken());
    setReady(true);
  }, []);

  if (!ready) return null;
  if (!token) return <AdminLogin onLogin={setToken} />;

  const role = roleFromToken(token);
  const activeGroup = TAB_GROUPS.find((g) => (g.tabs as readonly string[]).includes(tab));

  return (
    <main className="min-h-screen bg-indigo-950 text-indigo-100">
      <header className="flex items-center gap-3 border-b-[3px] border-magenta-500 bg-indigo-900 px-4 py-3">
        <button
          aria-label="Open sections"
          className="flex h-9 w-9 items-center justify-center rounded-lg border-[1.5px] border-indigo-600 bg-indigo-950 text-lg text-indigo-100 lg:hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-magenta-500"
          onClick={() => setNavOpen(true)}
        >
          ☰
        </button>
        <h1 className="font-display text-lg font-bold tracking-wide text-white">
          🥞 DOSADASH <span className="text-magenta-400">BACKOFFICE</span>
        </h1>
        <span className="ml-auto flex items-center gap-2.5">
          {role && <Badge tone="brass">{role}</Badge>}
          <Btn
            variant="ghost"
            size="sm"
            onClick={() => {
              clearAdminToken();
              setToken(null);
            }}
          >
            Logout
          </Btn>
        </span>
      </header>

      {/* Small screens: the same grouped nav as a left drawer */}
      {navOpen && (
        <div
          className="fixed inset-0 z-50 bg-indigo-950/70 lg:hidden"
          onClick={() => setNavOpen(false)}
        >
          <div
            className="animate-fade-up h-full w-64 overflow-y-auto border-r-2 border-indigo-700 bg-indigo-950 px-3 py-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between px-2.5">
              <span className="font-display text-sm font-bold text-white">Sections</span>
              <button
                aria-label="Close sections"
                className="text-indigo-200 transition-colors duration-150 hover:text-turmeric-400"
                onClick={() => setNavOpen(false)}
              >
                ✕
              </button>
            </div>
            <GroupedNav
              tab={tab}
              onSelect={(t) => {
                setTab(t);
                setNavOpen(false);
              }}
            />
          </div>
        </div>
      )}

      <div className="mx-auto flex max-w-[1380px] items-start">
        {/* Desktop: vertical grouped sidebar */}
        <aside className="sticky top-0 hidden max-h-screen w-56 shrink-0 overflow-y-auto border-r border-indigo-800 px-3 py-4 lg:block">
          <GroupedNav tab={tab} onSelect={setTab} />
        </aside>

        <div className="min-w-0 flex-1">
          <section className="p-4 sm:px-6" key={tab}>
            <div className="mb-3">
              {activeGroup && <Eyebrow>{activeGroup.label}</Eyebrow>}
              <SectionHeading as="h2" className="inline-block text-xl text-white">
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
          {tab === "Feedback" && <FeedbackTab />}
          {tab === "Settings" && <SettingsTab />}
          {tab === "Audit" && <AuditTab />}
        </Card>
          </section>
        </div>
      </div>
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
    <main className="flex min-h-screen items-center justify-center bg-indigo-950">
      <Card tone="dark" className="w-80 p-6">
        <div className="mb-4 font-display text-lg font-bold tracking-wide text-white">
          🥞 DOSADASH <span className="text-magenta-400">BACKOFFICE</span>
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
            <Btn variant="turmeric" className="w-full py-2" onClick={request}>
              Send OTP
            </Btn>
          </>
        ) : (
          <>
            {demoOtp ? (
              <p className="mb-2 text-sm text-indigo-200">📟 Demo mode — your OTP is <b className="font-display text-turmeric-400">{demoOtp}</b></p>
            ) : (
              <p className="mb-2 text-sm text-indigo-200">✈️ OTP sent to your linked Telegram</p>
            )}
            <Input
              tone="dark"
              className="mb-3 w-full py-2"
              placeholder="6-digit OTP"
              value={otp}
              onChange={(e) => setOtp(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && otp.length === 6 && verify()}
            />
            <Btn variant="turmeric" className="w-full py-2" disabled={otp.length !== 6} onClick={verify}>
              Sign in
            </Btn>
          </>
        )}
        {error && <p className="mt-3 text-sm font-semibold text-[#FF8B8B]">⚠ {error}</p>}
      </Card>
    </main>
  );
}
