/** Bundle budget for the customer home page (docs/17 §6, A1).
 *
 *  Runs against the build output, so `npm run build` must come first (CI's
 *  "Web build" job does build → test). Skips when there is no build.
 *
 *  Two promises are checked:
 *   1. three.js is NOT in the scripts the home page needs to load — it must
 *      stay behind the dynamic import in Scene3D.tsx.
 *   2. Those first-load scripts stay inside the budget. The number is the
 *      gzip sum of the route's chunks; Next's own "First Load JS" column is
 *      measured slightly differently and reads ~3 kB higher.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";
import { gzipSync } from "node:zlib";

const NEXT = ".next";
const MANIFEST = join(NEXT, "app-build-manifest.json");
/** Pre-redesign baseline was ~117 kB by this measure (120 kB in Next's column). */
const HOME_FIRST_LOAD_BUDGET_KB = 128;
const THREE_MARKER = "WebGLRenderer";

const built = existsSync(MANIFEST);
const firstLoad: string[] = built
  ? [...new Set<string>(JSON.parse(readFileSync(MANIFEST, "utf8")).pages["/page"])].filter((f) => f.endsWith(".js"))
  : [];

test("home page first-load JS stays within budget", { skip: !built && "no build output" }, () => {
  const kb = firstLoad.reduce((sum, f) => sum + gzipSync(readFileSync(join(NEXT, f))).length, 0) / 1024;
  assert.ok(
    kb <= HOME_FIRST_LOAD_BUDGET_KB,
    `home first-load JS is ${kb.toFixed(1)} kB gzip, budget ${HOME_FIRST_LOAD_BUDGET_KB} kB`,
  );
});

test("three.js is never in the home page's first-load scripts", { skip: !built && "no build output" }, () => {
  const leaked = firstLoad.filter((f) => readFileSync(join(NEXT, f)).includes(THREE_MARKER));
  assert.deepEqual(leaked, [], "three.js leaked into first-load chunks — keep it behind the dynamic import");
});

test("three.js is still shipped, as an async chunk", { skip: !built && "no build output" }, () => {
  const dir = join(NEXT, "static", "chunks");
  const async = readdirSync(dir)
    .filter((f) => f.endsWith(".js") && !firstLoad.some((x) => x.endsWith(f)))
    .filter((f) => readFileSync(join(dir, f)).includes(THREE_MARKER));
  assert.ok(async.length > 0, "no async chunk contains three.js — the live scene would never load");
});
