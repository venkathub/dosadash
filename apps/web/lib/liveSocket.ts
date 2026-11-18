/**
 * Shared live-socket client for KDS, /fixer and the order tracker.
 *
 * Why this exists: browsers hide the server's close code unless the upgrade was
 * accepted, and an *expired* JWT (30-min TTL) used to look like any other
 * network blip — the KDS reconnected forever instead of returning to login.
 * So auth failure is decided three ways, all deterministic:
 *   1. server close code 4401 (bad/expired) / 4403 (wrong role)
 *   2. the token's own `exp` claim is in the past (works even when the code is
 *      swallowed → 1006, e.g. by a proxy or an older server build)
 *   3. nothing else: any other close is a transient drop → backoff + retry.
 */

export type AuthFailure = "expired" | "forbidden";

/** Read `exp` (seconds) from a JWT without verifying it — presentational only. */
export function tokenExpiry(token: string): number | null {
  try {
    const part = token.split(".")[1];
    if (!part) return null;
    const json = atob(part.replace(/-/g, "+").replace(/_/g, "/"));
    const exp = (JSON.parse(json) as { exp?: unknown }).exp;
    return typeof exp === "number" ? exp : null;
  } catch {
    return null;
  }
}

/** True when the token is malformed or already past `exp` (small clock-skew grace). */
export function tokenExpired(token: string, nowMs: number = Date.now(), skewSec = 5): boolean {
  const exp = tokenExpiry(token);
  return exp === null || exp - skewSec <= nowMs / 1000;
}

/** Exponential backoff: base·2^attempt, capped. */
export function backoffDelay(attempt: number, baseMs = 2000, maxMs = 30_000): number {
  return Math.min(maxMs, baseMs * 2 ** Math.min(attempt, 10));
}

export type LiveSocketOptions = {
  path: string; // e.g. "/ws/kds"
  token: string;
  onMessage: (data: string) => void;
  onConnectedChange?: (connected: boolean) => void;
  onAuthFailure: (reason: AuthFailure) => void;
};

/** Open a self-reconnecting socket. Returns a disposer that stops everything. */
export function openLiveSocket(opts: LiveSocketOptions): () => void {
  let disposed = false;
  let attempt = 0;
  let ws: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const fail = (reason: AuthFailure) => {
    disposed = true;
    opts.onConnectedChange?.(false);
    opts.onAuthFailure(reason);
  };

  const connect = () => {
    if (disposed) return;
    if (tokenExpired(opts.token)) return fail("expired");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(
      `${proto}://${location.host}${opts.path}?token=${encodeURIComponent(opts.token)}`,
    );
    ws.onopen = () => {
      attempt = 0;
      opts.onConnectedChange?.(true);
    };
    ws.onmessage = (msg) => opts.onMessage(msg.data as string);
    ws.onclose = (e) => {
      if (disposed) return;
      opts.onConnectedChange?.(false);
      if (e.code === 4401) return fail("expired");
      if (e.code === 4403) return fail("forbidden");
      if (tokenExpired(opts.token)) return fail("expired"); // code swallowed (1006)
      timer = setTimeout(connect, backoffDelay(attempt++));
    };
  };

  connect();
  return () => {
    disposed = true;
    if (timer) clearTimeout(timer);
    if (ws) {
      ws.onclose = null;
      ws.close(1000);
    }
  };
}
