"use client";

// Tawa Noir hero stage (docs/17). Fills its parent (the hero banner).
// Server-renders ONLY the static backdrop and tawa photo, so the banner is
// complete without JavaScript, WebGL or a capable device. After mount it asks
// lib/scene.ts whether this visitor should get the live scene and, if so,
// pulls three.js in through a dynamic import (kept out of first-load JS).

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { readSceneEnv, shouldRender3D, type DISH_CUTOUTS } from "../../lib/scene";
import type { SceneHandle } from "./scene/tawaScene";
import { cx } from "./ui";

type DishKey = (typeof DISH_CUTOUTS)[number];
export type Scene3DHandle = { select(key: DishKey): void; hop(key: DishKey): void };

const Scene3D = forwardRef<Scene3DHandle>(function Scene3D(_props, ref) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<SceneHandle | null>(null);
  // "static" is both the SSR state and the permanent fallback.
  const [live, setLive] = useState(false);

  useImperativeHandle(ref, () => ({
    select: (key) => sceneRef.current?.select(key),
    hop: (key) => sceneRef.current?.hop(key),
  }));

  useEffect(() => {
    // Capability is read here, after mount — never during render (issue #120).
    const env = readSceneEnv();
    if (!shouldRender3D(env)) return;
    let cancelled = false;
    const start = () => {
      import("./scene/tawaScene")
        .then(({ mountTawaScene }) => {
          const canvas = canvasRef.current;
          if (cancelled || !canvas) return;
          sceneRef.current = mountTawaScene(canvas, {
            backdrop: backdropRef.current,
            reducedMotion: env.reducedMotion,
            onReady: () => {
              if (!cancelled) setLive(true);
            },
            onLost: () => setLive(false),
          });
        })
        // No WebGL, a blocked chunk, a driver bug: the static banner is the answer.
        .catch(() => setLive(false));
    };
    // Let the page paint and become interactive first.
    const useIdle = typeof window.requestIdleCallback === "function";
    const id = useIdle ? window.requestIdleCallback(start, { timeout: 1500 }) : window.setTimeout(start, 300);
    return () => {
      cancelled = true;
      if (useIdle) window.cancelIdleCallback(id);
      else window.clearTimeout(id);
      sceneRef.current?.dispose();
      sceneRef.current = null;
    };
  }, []);

  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden bg-soot-950">
      <div ref={backdropRef} className="tawa-backdrop absolute -inset-[5%]" />
      {/* Static art: the LCP element and the no-WebGL fallback. Sized by the
          banner, so it cannot shift layout. Fades out once the scene draws. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/scene/tawa.webp"
        alt=""
        width={1024}
        height={1024}
        fetchPriority="high"
        className={cx(
          "absolute bottom-[-8%] left-1/2 h-[112%] w-auto max-w-none -translate-x-1/2 transition-opacity duration-700 lg:left-[72%]",
          live ? "opacity-0" : "opacity-100",
        )}
      />
      <canvas
        ref={canvasRef}
        className={cx("absolute inset-0 h-full w-full transition-opacity duration-700", live ? "opacity-100" : "opacity-0")}
      />
    </div>
  );
});

export default Scene3D;
