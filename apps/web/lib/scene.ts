// Tawa Noir scene support (docs/17). Pure functions only — unit-tested in
// scene.test.mts and safe to import from server or client code. Nothing here
// touches three.js; the WebGL module is loaded separately and lazily.

/** What we can learn about the device, read once after mount. */
export type SceneEnv = {
  reducedMotion: boolean;
  saveData: boolean;
  /** navigator.deviceMemory in GB (Chromium only) — null when the browser hides it. */
  deviceMemoryGb: number | null;
  /** navigator.hardwareConcurrency — null when unknown. */
  cores: number | null;
};

/**
 * Who gets the live WebGL scene. Everyone else gets the static backdrop and
 * hero image, which is a complete page on its own.
 *
 * OWNER DECISION POINT (docs/17 §2.5): this is a reach-vs-jank trade-off.
 * The defaults below are deliberately conservative; loosen or tighten them
 * once there are real-device numbers.
 */
export function shouldRender3D(env: SceneEnv): boolean {
  if (env.reducedMotion) return false; // an accessibility setting, never overridden
  if (env.saveData) return false; // the visitor asked for less data
  if (env.deviceMemoryGb !== null && env.deviceMemoryGb < 4) return false;
  if (env.cores !== null && env.cores < 4) return false;
  return true; // unknown capability counts as capable (Safari hides both numbers)
}

/** Browser-only. Call inside an effect, never during render (issue #120). */
export function readSceneEnv(): SceneEnv {
  const nav = navigator as Navigator & {
    deviceMemory?: number;
    connection?: { saveData?: boolean };
  };
  return {
    reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    saveData: nav.connection?.saveData === true,
    deviceMemoryGb: typeof nav.deviceMemory === "number" ? nav.deviceMemory : null,
    cores: typeof nav.hardwareConcurrency === "number" ? nav.hardwareConcurrency : null,
  };
}

/**
 * Photo cutouts in public/scene/. `b`/`t` = empty margin below/above the
 * subject, `cx` = horizontal offset of the subject's centre, `w` = subject
 * width — all as fractions of the square image, measured from the alpha
 * channel so each dish's base sits exactly on the counter.
 */
export const CUTOUT_META = {
  "masala-dosa": { b: 0.21, t: 0.26, cx: 0.008, w: 0.908 },
  idli: { b: 0.192, t: 0.222, cx: -0.005, w: 0.96 },
  "filter-coffee": { b: 0.149, t: 0.137, cx: -0.007, w: 0.713 },
  "cheese-dosa": { b: 0.199, t: 0.229, cx: -0.009, w: 0.923 },
  "masala-chai": { b: 0.149, t: 0.162, cx: -0.012, w: 0.687 },
  tawa: { b: 0.129, t: 0.222, cx: -0.005, w: 0.986 },
} as const;

export type CutoutKey = keyof typeof CUTOUT_META;

/** Turntable order. The tawa is the centrepiece, not a dish. */
export const DISH_CUTOUTS: Exclude<CutoutKey, "tawa">[] = [
  "masala-dosa",
  "idli",
  "filter-coffee",
  "cheese-dosa",
  "masala-chai",
];

const NAME_TO_CUTOUT: Record<string, Exclude<CutoutKey, "tawa">> = {
  "masala dosa": "masala-dosa",
  "idli (2 pcs)": "idli",
  idli: "idli",
  "filter coffee": "filter-coffee",
  "cheese dosa": "cheese-dosa",
  "masala chai": "masala-chai",
};

/**
 * Which cutout (if any) pictures this dish. Matching is on the ENGLISH name,
 * so pass `canonical_name ?? name` — a translated menu must still resolve.
 * Exact match only: "Mysore Masala Dosa" must never borrow Masala Dosa's photo.
 */
export function cutoutKeyFor(englishName: string): Exclude<CutoutKey, "tawa"> | null {
  return NAME_TO_CUTOUT[englishName.trim().toLowerCase()] ?? null;
}

/** Shortest signed rotation (radians) to bring slot `to` to the front from `from`. */
export function shortestTurn(from: number, to: number, slots: number): number {
  let d = (to - from) % slots;
  if (d > slots / 2) d -= slots;
  if (d < -slots / 2) d += slots;
  return d;
}
