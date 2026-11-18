"use client";

export type MenuItem = {
  id: number;
  name: string;
  category: string;
  description: string | null;
  price: string;
  is_veg: boolean;
  spice_level: number;
  meal_periods: string[];
  allergens: string[];
  image_url: string | null;
  image_ai: boolean; // AI-generated photo — always shown with an AI badge
  canonical_name: string | null; // English name when a translation is applied
  category_label: string | null; // localized section heading
  available_now: boolean; // false = outside this dish's serving windows right now
  serving_windows: string | null; // server-built human text (e.g. "6–11:30 AM & 5–10 PM"); null = always available
  protein_g: number | null; // per serving, owner-approved estimate; null = not scored yet (never claim a number)
};

export type OrderItem = { item_id: number; name: string; qty: number; unit_price: string };

export type Order = {
  id: number;
  status: string;
  subtotal: string;
  discount?: string;
  coupon_code?: string | null;
  gst: string;
  total: string;
  placed_at: string;
  items: OrderItem[];
  payment: {
    provider: string;
    provider_order_id: string | null;
    status: string;
    signature_verified: boolean;
  } | null;
};

export type User = { id: number; phone: string; name: string | null; role: string };

const TOKEN_KEY = "dosadash_token";
const USER_KEY = "dosadash_user";

export const getToken = () => (typeof window === "undefined" ? null : localStorage.getItem(TOKEN_KEY));
export const getUser = (): User | null => {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  return raw ? JSON.parse(raw) : null;
};
export const saveSession = (token: string, user: User) => {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
};
export const clearSession = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
};

/** Fired when an authed call 401s, so pages holding `user` state can sign out. */
export const SESSION_EXPIRED_EVENT = "dosadash:session-expired";

/** Drop the dead session and tell mounted pages (they keep `user` in state). */
export const expireSession = () => {
  clearSession();
  if (typeof window !== "undefined") window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

export async function api<T>(path: string, opts: { method?: string; body?: unknown; auth?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (opts.auth) {
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  const resp = await fetch(`/api/v1${path}`, {
    method: opts.method ?? "GET",
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  });
  // Non-JSON bodies (a mid-deploy 502 page) must not mask the status code.
  const data = resp.status === 204 ? null : await resp.json().catch(() => null);
  // Expired/revoked token on an authed call: sign out everywhere so the UI
  // falls back to logged-out instead of failing every request with 401.
  if (resp.status === 401 && opts.auth) expireSession();
  if (!resp.ok) throw new ApiError(resp.status, data?.detail ?? `HTTP ${resp.status}`);
  return data as T;
}
