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

export type AdminItem = {
  id: number;
  name: string;
  category: string;
  price: string;
  is_veg: boolean;
  spice_level: number;
  is_available: boolean;
  schedule: Record<string, { start: string; end: string }> | null;
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
