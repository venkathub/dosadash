"use client";

/** Admin-surface API helper — own token (admin_token) like the KDS surface. */

export class AdminApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

const TOKEN_KEY = "admin_token";

export const getAdminToken = () =>
  typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY);
export const saveAdminToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearAdminToken = () => localStorage.removeItem(TOKEN_KEY);

export async function adminApi<T>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getAdminToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(`/api/v1${path}`, {
    method: opts.method ?? "GET",
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  const data = resp.status === 204 ? null : await resp.json().catch(() => null);
  if (!resp.ok) {
    const detail = (data as { detail?: string } | null)?.detail ?? `HTTP ${resp.status}`;
    throw new AdminApiError(resp.status, detail);
  }
  return data as T;
}

/* ---------------------------------------------------------------- types */

export type ScheduleWindow = { start: string; end: string };

export type AdminItem = {
  id: number;
  name: string;
  category: string;
  price: string;
  is_veg: boolean;
  spice_level: number;
  is_available: boolean;
  // Per weekday: legacy single window OR a multi-window list (Phase 11).
  schedule: Record<string, ScheduleWindow | ScheduleWindow[]> | null;
  allergens: string[];
};

export type AdminOrder = {
  id: number;
  status: string;
  channel: string;
  total: string;
  placed_at: string;
  items: { name: string; qty: number }[];
  payment: { status: string; refund_id: string | null } | null;
};

export type Combo = {
  id: number;
  name: string;
  item_ids: number[];
  price: string;
  source: string;
  status: string;
};

export type Nutrition = {
  item_id: number;
  status: string;
  model: string;
  prompt_version: string;
  estimate: {
    calories_kcal: number;
    protein_g: number;
    carbs_g: number;
    fat_g: number;
    fiber_g: number;
    confidence: number;
    notes: string | null;
  };
};

export type SettingsRow = {
  business_hours: Record<string, { start: string; end: string }> | null;
  delivery_pincodes: string[];
  kitchen_paused: boolean;
};

export type AuditRow = {
  id: number;
  user_id: number;
  action: string;
  entity: string;
  detail: Record<string, unknown> | null;
  at: string;
};

export type EvalRun = {
  id: number;
  ran_at: string;
  git_sha: string | null;
  trigger: string;
  cases: number;
  order_accuracy: number;
  tool_correctness: number;
  guardrail_bypasses: number;
  guardrail_cases: number;
  tone: number | null;
  gates_passed: boolean;
  failures: string[];
};

export type EvalCaseReport = {
  id: string;
  tags: string[];
  language: string;
  accuracy_problems: string[];
  tool_violations: string[];
  bypasses: string[];
};

export type EvalRunDetail = EvalRun & { case_reports: EvalCaseReport[] };

export type ModelDailyCost = {
  model: string;
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  calls: number;
};

export type DailyCost = {
  date: string;
  traces: number;
  observations: number;
  cost_usd: number;
  models: ModelDailyCost[];
};

export type CostSummary = {
  configured: boolean;
  days: DailyCost[];
  total_cost_usd: number;
};

/* ------------------------------------------------ cache stats (Phase 9) */

export type SemcacheStats = {
  exact_hits: number;
  semantic_hits: number;
  misses: number;
  stores: number;
  flushes: number;
  lookups: number;
  hit_rate: number;
};

export type PromptCacheStats = {
  calls: number;
  prompt_tokens: number;
  cached_prompt_tokens: number;
  completion_tokens: number;
  cached_share: number;
};

export type CacheStats = {
  semcache: SemcacheStats;
  prompt_cache: PromptCacheStats;
  semcache_enabled: boolean;
  semcache_threshold: number;
  semcache_ttl_seconds: number;
};

/* ---------------------------------------------------- reports + CRM (Phase 5) */

/** Authenticated raw-text fetch (CSV downloads). */
export async function adminApiText(path: string): Promise<string> {
  const headers: Record<string, string> = {};
  const token = getAdminToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(`/api/v1${path}`, { headers });
  if (!resp.ok) throw new AdminApiError(resp.status, `HTTP ${resp.status}`);
  return resp.text();
}

export type SalesBucket = { period: string; orders: number; revenue: number; gst: number; aov: number };
export type SalesReport = {
  granularity: "daily" | "weekly" | "monthly";
  days: number;
  buckets: SalesBucket[];
  total_orders: number;
  total_revenue: number;
  total_gst: number;
};

export type DishPnlRow = {
  item_id: number;
  name: string;
  category: string;
  qty: number;
  revenue: number;
  ingredient_cost: number;
  cost_source: "recipe" | "estimated";
  margin: number;
  margin_pct: number;
};
export type DishPnlReport = { days: number; rows: DishPnlRow[] };

export type ForecastPoint = {
  date: string;
  forecast_qty: number | null;
  actual_qty: number | null;
  anomaly: boolean;
};
export type DishAnomaly = {
  item_id: number;
  name: string;
  date: string;
  forecast_qty: number;
  actual_qty: number;
  deviation_pct: number;
};
export type ForecastReport = {
  points: ForecastPoint[];
  dish_anomalies: DishAnomaly[];
  model_version: string | null;
};

export type CrmTier = { tier: string; users: number; avg_churn_risk: number; total_ltv: number };
export type CrmUser = {
  user_id: number;
  name: string | null;
  phone: string;
  rfm_tier: string;
  churn_risk: number;
  ltv: number;
};
export type CrmReport = { computed_at: string | null; tiers: CrmTier[]; at_risk: CrmUser[] };

// ---- feedback (Phase 13 self-healing loop)
export type AdminFeedback = {
  id: number;
  user_id: number | null;
  reporter_tier: "ANON" | "CUSTOMER" | "STAFF" | "SYSTEM";
  type: "BUG" | "FEATURE";
  status: string;
  title: string;
  description: string;
  context: Record<string, string> | null;
  dedupe_hash: string;
  github_issue_number: number | null;
  github_error: string | null;
  triage: { verdict?: string; effort?: string; risk?: string; model?: string; prompt_version?: string } | null;
  created_at: string;
  updated_at: string;
};
export type AdminFeedbackList = { reports: AdminFeedback[]; total: number; github_repo: string };
