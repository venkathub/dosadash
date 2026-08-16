"use client";

export type MenuItem = {
  id: number;
  name: string;
  category: string;
  description: string | null;
  price: string;
  is_veg: boolean;
  spice_level: number;
  allergens: string[];
};

export type OrderItem = { item_id: number; name: string; qty: number; unit_price: string };

export type Order = {
  id: number;
  status: string;
  subtotal: string;
  gst: string;
  total: string;
  placed_at: string;
  items: OrderItem[];
  payment: { provider: string; status: string; signature_verified: boolean } | null;
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
  const data = resp.status === 204 ? null : await resp.json();
  if (!resp.ok) throw new ApiError(resp.status, data?.detail ?? `HTTP ${resp.status}`);
  return data as T;
}

export const wsUrl = (path: string, token: string) => {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}?token=${token}`;
};
