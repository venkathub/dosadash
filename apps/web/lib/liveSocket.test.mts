/** Run: `npm test` (node's built-in runner; no extra dependencies). */
import assert from "node:assert/strict";
import { test } from "node:test";
import { backoffDelay, tokenExpired, tokenExpiry } from "./liveSocket.ts";

const jwt = (payload: object) =>
  `h.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.s`;
const NOW = 1_800_000_000_000;

test("token with future exp is not expired", () => {
  assert.equal(tokenExpired(jwt({ exp: NOW / 1000 + 600 }), NOW), false);
});

test("past exp, within-skew exp, and malformed tokens count as expired", () => {
  assert.equal(tokenExpired(jwt({ exp: NOW / 1000 - 1 }), NOW), true);
  assert.equal(tokenExpired(jwt({ exp: NOW / 1000 + 3 }), NOW), true); // < 5s skew grace
  for (const bad of ["", "garbage", "a.b.c", jwt({ sub: "1" })]) {
    assert.equal(tokenExpired(bad, NOW), true, bad);
  }
});

test("tokenExpiry reads exp and tolerates base64url without padding", () => {
  assert.equal(tokenExpiry(jwt({ exp: 123 })), 123);
  assert.equal(tokenExpiry(jwt({ exp: "soon" })), null);
});

test("backoff doubles from 1s and caps at 8s", () => {
  assert.deepEqual([0, 1, 2, 3, 4, 20].map((n) => backoffDelay(n)), [1000, 2000, 4000, 8000, 8000, 8000]);
});
