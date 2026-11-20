// Tawa Noir live scene (docs/17 §2.4). Imported ONLY through a dynamic
// import() from Scene3D.tsx, so three.js stays out of the first-load bundle.
//
// The dishes are generated photo cutouts standing on the counter as
// camera-facing cards. The 3D is in the staging: depth, parallax, a slowly
// turning table, counter reflections, steam and embers.
//
// The scene lives in a compact HERO BANNER, not behind the page: ordering
// comes first, so nothing meaningful depends on it and it stops rendering
// when scrolled out of view. It is decoration and is aria-hidden.

import * as THREE from "three";
import { CUTOUT_META, DISH_CUTOUTS, shortestTurn, type CutoutKey } from "../../../lib/scene";

type DishKey = (typeof DISH_CUTOUTS)[number];

export type SceneHandle = {
  /** Turn the table so this dish faces the camera. */
  select(key: DishKey): void;
  /** The dish hops and (motion allowed) a copy arcs into the tray. */
  hop(key: DishKey): void;
  dispose(): void;
};

export type SceneOptions = {
  /** Backdrop element to parallax (the CSS kitchen image). */
  backdrop: HTMLElement | null;
  reducedMotion: boolean;
  /** Fired once every texture has loaded and the first frame is drawn. */
  onReady: () => void;
  /** Fired when the browser takes the GL context away; the caller falls back. */
  onLost: () => void;
};

type Card = {
  holder: THREE.Group;
  face: THREE.Group;
  mat: THREE.MeshBasicMaterial;
  reflMat: THREE.MeshBasicMaterial;
  shMat: THREE.MeshBasicMaterial;
  top: number;
};
type Dish = Card & { hop: number; flip: number; lum: number };

const RADIUS = 4.5;
const STEP = (Math.PI * 2) / DISH_CUTOUTS.length;
const OPENED_FLAG = "tawa_opening_played";

const ease = (x: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, x)), 3);

function canvasTexture(w: number, h: number, draw: (g: CanvasRenderingContext2D) => void) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const g = c.getContext("2d");
  if (g) draw(g);
  return new THREE.CanvasTexture(c);
}

export function mountTawaScene(canvas: HTMLCanvasElement, opts: SceneOptions): SceneHandle {
  const reduced = opts.reducedMotion;
  const disposables: { dispose(): void }[] = [];
  const track = <T extends { dispose(): void }>(x: T) => (disposables.push(x), x);

  // Throws when WebGL is unavailable — Scene3D catches it and keeps the static page.
  const renderer = new THREE.WebGLRenderer({
    canvas,
    antialias: true,
    alpha: true,
    powerPreference: "high-performance",
  });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setClearColor(0x000000, 0);
  const scene = new THREE.Scene();
  const FOV = 34;
  const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 100);

  // ── textures ──
  let loadedAt: number | null = null;
  let ready = false;
  const manager = new THREE.LoadingManager();
  manager.onLoad = () => {
    loadedAt = performance.now();
  };
  const loader = new THREE.TextureLoader(manager);
  const photo = (key: CutoutKey) => {
    const t = track(loader.load(`/scene/${key}.webp`));
    t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 8;
    return t;
  };
  const puff = track(
    canvasTexture(128, 128, (g) => {
      const r = g.createRadialGradient(64, 64, 2, 64, 64, 64);
      r.addColorStop(0, "rgba(255,255,255,0.9)");
      r.addColorStop(0.4, "rgba(255,255,255,0.35)");
      r.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = r;
      g.fillRect(0, 0, 128, 128);
    }),
  );
  const blob = track(
    canvasTexture(256, 128, (g) => {
      g.scale(1, 0.5);
      const r = g.createRadialGradient(128, 128, 8, 128, 128, 128);
      r.addColorStop(0, "rgba(0,0,0,0.85)");
      r.addColorStop(1, "rgba(0,0,0,0)");
      g.fillStyle = r;
      g.fillRect(0, 0, 256, 256);
    }),
  );
  // Reflection fade: opaque at the contact line (image bottom), gone by mid-height.
  const fade = track(
    canvasTexture(8, 256, (g) => {
      const r = g.createLinearGradient(0, 256, 0, 110);
      r.addColorStop(0, "#fff");
      r.addColorStop(0.25, "#bbb");
      r.addColorStop(1, "#000");
      g.fillStyle = "#000";
      g.fillRect(0, 0, 8, 256);
      g.fillStyle = r;
      g.fillRect(0, 110, 8, 146);
    }),
  );

  function makeCard(key: CutoutKey, size: number): Card {
    const m = CUTOUT_META[key];
    const map = photo(key);
    const geo = track(new THREE.PlaneGeometry(size, size));
    const mat = track(
      new THREE.MeshBasicMaterial({ map, transparent: true, depthWrite: false, side: THREE.DoubleSide }),
    );
    const main = new THREE.Mesh(geo, mat);
    main.position.set(-m.cx * size, size * (0.5 - m.b), 0);
    const reflMat = track(
      new THREE.MeshBasicMaterial({
        map,
        alphaMap: fade,
        transparent: true,
        opacity: 0.2,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    );
    const refl = new THREE.Mesh(geo, reflMat);
    refl.scale.y = -1;
    refl.position.set(-m.cx * size, -size * (0.5 - m.b), 0);
    const shMat = track(
      new THREE.MeshBasicMaterial({ map: blob, transparent: true, opacity: 0.9, depthWrite: false }),
    );
    const sh = new THREE.Mesh(track(new THREE.PlaneGeometry(size * m.w * 1.25, size * m.w * 0.5)), shMat);
    sh.rotation.x = -Math.PI / 2;
    sh.position.y = 0.01;
    const face = new THREE.Group();
    face.add(refl, main);
    const holder = new THREE.Group();
    holder.add(sh, face);
    scene.add(holder);
    return { holder, face, mat, reflMat, shMat, top: size * (1 - m.b - m.t) };
  }

  // ── stage ──
  const tawa = makeCard("tawa", 4.4);
  const haloMat = track(
    new THREE.SpriteMaterial({
      map: puff,
      color: 0xff6a1a,
      transparent: true,
      opacity: 0,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    }),
  );
  const halo = new THREE.Sprite(haloMat);
  halo.position.set(0, 1.0, -0.6);
  halo.scale.set(12, 7, 1);
  halo.renderOrder = 90;
  scene.add(halo);

  const dishes: Dish[] = DISH_CUTOUTS.map((key) => ({ ...makeCard(key, 2.5), hop: 0, flip: 0, lum: 0.5 }));

  // ── steam + embers ──
  type Steamer = (dt: number, t: number) => void;
  function makeSteam(
    parent: THREE.Object3D,
    origin: THREE.Vector3,
    count: number,
    spread: number,
    rise: number,
    scale: number,
  ): Steamer {
    const sprites: { s: THREE.Sprite; m: THREE.SpriteMaterial; life: number; seed: number }[] = [];
    for (let i = 0; i < count; i++) {
      const m = track(
        new THREE.SpriteMaterial({ map: puff, transparent: true, opacity: 0, depthWrite: false, color: 0xfff1dc }),
      );
      const s = new THREE.Sprite(m);
      s.renderOrder = 400;
      parent.add(s);
      sprites.push({ s, m, life: Math.random(), seed: Math.random() * 10 });
    }
    return (dt, t) => {
      for (const u of sprites) {
        u.life += dt * (0.14 + (u.seed % 1) * 0.08);
        if (u.life > 1) u.life = 0;
        const k = u.life;
        u.s.position.set(
          origin.x + Math.sin(t * 0.7 + u.seed) * spread * (0.4 + k),
          origin.y + k * rise,
          origin.z + 0.05 + Math.cos(t * 0.5 + u.seed * 2) * spread * 0.3,
        );
        const sc = (0.3 + k * 1.4) * scale;
        u.s.scale.set(sc, sc, sc);
        u.m.opacity = Math.sin(k * Math.PI) * 0.2;
      }
    };
  }
  const steamers: Steamer[] = [makeSteam(scene, new THREE.Vector3(0, tawa.top * 0.72, 0), 36, 1.0, 3.4, 1.2)];
  for (const key of ["filter-coffee", "masala-chai"] as const) {
    const d = dishes[DISH_CUTOUTS.indexOf(key)];
    steamers.push(makeSteam(d.holder, new THREE.Vector3(0, d.top * 0.95, 0), 8, 0.14, 1.5, 0.6));
  }

  const EMBERS = 200;
  const ePos = new Float32Array(EMBERS * 3);
  const eSeed = new Float32Array(EMBERS);
  for (let i = 0; i < EMBERS; i++) eSeed[i] = Math.random() * 100;
  const eGeo = track(new THREE.BufferGeometry());
  eGeo.setAttribute("position", new THREE.BufferAttribute(ePos, 3));
  const embers = new THREE.Points(
    eGeo,
    track(
      new THREE.PointsMaterial({
        color: 0xff8a3c,
        size: 0.1,
        map: puff,
        transparent: true,
        opacity: 0.9,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    ),
  );
  embers.renderOrder = 410;
  scene.add(embers);
  function updateEmbers(t: number) {
    for (let i = 0; i < EMBERS; i++) {
      const s = eSeed[i];
      const k = (t * 0.05 + s) % 1;
      const a = s * 6.283 + t * 0.08;
      const rad = 0.8 + (s % 6);
      ePos[i * 3] = Math.cos(a) * rad;
      ePos[i * 3 + 1] = 0.4 + k * 6 + Math.sin(t + s) * 0.1;
      ePos[i * 3 + 2] = Math.sin(a) * rad;
    }
    eGeo.attributes.position.needsUpdate = true;
  }

  // ── sizing, pointer, visibility ──
  let mx = 0;
  let my = 0;
  let width = 1;
  let height = 1;
  const camPos = new THREE.Vector3();
  const camLook = new THREE.Vector3();
  function resize() {
    width = Math.max(1, canvas.clientWidth);
    height = Math.max(1, canvas.clientHeight);
    renderer.setSize(width, height, false);
    const aspect = width / height;
    camera.aspect = aspect;
    camera.updateProjectionMatrix();
    // Frame the table. Narrow banners pull the camera back so the ring fits;
    // wide banners truck the camera left so the table sits in the right half,
    // leaving the left for the headline (a truck, not a pan: no skew).
    const tan = Math.tan((FOV * Math.PI) / 360);
    const dist = Math.max(11.5, 12.5 / (2 * tan * aspect));
    const shift = aspect > 2.4 ? -(2 * dist * tan * aspect) * 0.2 : 0;
    camPos.set(shift, 3.3, dist);
    camLook.set(shift, 1.15, 0);
  }
  const onPointer = (e: PointerEvent) => {
    mx = e.clientX / window.innerWidth - 0.5;
    my = e.clientY / window.innerHeight - 0.5;
  };
  const sizeObserver = new ResizeObserver(resize);
  sizeObserver.observe(canvas);
  if (!reduced) window.addEventListener("pointermove", onPointer);
  // Do no work while the banner is scrolled away (most of an ordering session).
  let onScreen = true;
  const viewObserver = new IntersectionObserver(([entry]) => {
    onScreen = entry.isIntersecting;
    if (onScreen) wake();
  });
  viewObserver.observe(canvas);

  // ── opening: scene only, once per browser session; the UI never waits for it ──
  let playOpening = !reduced && window.scrollY < 40;
  let lastSelect = -1e9;
  try {
    if (sessionStorage.getItem(OPENED_FLAG)) playOpening = false;
    else sessionStorage.setItem(OPENED_FLAG, "1");
  } catch {
    /* storage blocked: just play it */
  }

  // ── frame loop ──
  const va = new THREE.Vector3();
  let sel = 0;
  let ringTarget = 0;
  let ringRot = 0;
  let ringPrev = 0;
  let last = performance.now();
  let t = 0;
  let raf = 0;
  let running = true;
  let disposed = false;

  const yaw = (o: THREE.Object3D) =>
    Math.atan2(camera.position.x - o.position.x, camera.position.z - o.position.z);

  function frame() {
    raf = 0;
    if (!running || !onScreen || disposed) return;
    const now = performance.now();
    const dt = Math.min((now - last) / 1000, 0.05);
    last = now;
    t += dt;
    const it = !playOpening ? 99 : loadedAt === null ? 0 : (now - loadedAt) / 1000;
    const damp = reduced ? 1 : 1 - Math.exp(-dt * 3.2);

    const push = 1 - ease((it - 0.2) / 3.0); // opening dolly-in
    camera.position.set(camPos.x + mx * 0.6 - push * 2, camPos.y - my * 0.35 + push * 1.6, camPos.z + push * 9);
    camera.lookAt(camLook);
    camera.rotation.z += push * 0.05 + (reduced ? 0 : Math.sin(t * 0.25) * 0.003);
    if (opts.backdrop) {
      opts.backdrop.style.transform = `translate3d(${(-mx * 18).toFixed(1)}px, ${(-my * 8).toFixed(1)}px, 0) scale(${(1.08 + push * 0.1).toFixed(3)})`;
    }

    // The table drifts on its own; picking a dish brings it to the front and
    // holds it there for a few seconds before the drift resumes.
    if (!reduced && now - lastSelect > 4500) ringTarget -= dt * 0.1;
    ringRot += (ringTarget - ringRot) * (reduced ? 1 : 1 - Math.exp(-dt * 4));
    const vel = (ringRot - ringPrev) / Math.max(dt, 1e-3);
    ringPrev = ringRot;

    const te = ease((it - 0.3) / 1.4);
    tawa.holder.position.y = (1 - te) * 7;
    tawa.face.rotation.set(0, yaw(tawa.holder), (1 - te) * -1.1);
    tawa.mat.opacity = te;
    tawa.reflMat.opacity = 0.2 * te;
    tawa.shMat.opacity = 0.9 * te;
    tawa.holder.traverse((o) => (o.renderOrder = 200));

    dishes.forEach((d, k) => {
      const a = k * STEP + ringRot;
      const front = Math.cos(a);
      const de = ease((it - 1.1 - k * 0.16) / 1.2); // staggered fly-in from depth
      d.hop = Math.max(0, d.hop - dt * 1.8);
      d.flip = Math.max(0, d.flip - dt * 1.25);
      const rad = RADIUS * (1 + (1 - de) * 2.6);
      d.holder.position.set(Math.sin(a) * rad, Math.sin(d.hop * Math.PI) * 0.8 + (1 - de) * 4, Math.cos(a) * rad);
      d.face.position.y = reduced ? 0 : Math.sin(t * 0.9 + k * 1.3) * 0.04;
      // Side dishes turn edge-on like plates on a real turntable, the table banks
      // as it moves, and a picked dish spins once as it arrives at the front.
      d.face.rotation.set(
        0,
        yaw(d.holder) - Math.sin(a) * 0.55 + d.flip * d.flip * Math.PI * 2,
        reduced ? 0 : Math.max(-0.3, Math.min(0.3, -vel * 0.12)) + (1 - de) * 0.9,
      );
      const focus = 0.5 + 0.5 * Math.max(0, front); // whatever is in front is lit
      d.lum += (focus - d.lum) * damp;
      d.mat.color.setScalar(d.lum);
      d.reflMat.color.setScalar(d.lum);
      d.mat.opacity = de;
      d.reflMat.opacity = 0.2 * de;
      d.shMat.opacity = 0.9 * de;
      const sc = 0.82 + 0.3 * d.lum;
      d.holder.scale.lerp(va.set(sc, sc, sc), damp);
      const order = Math.round(front * 100) + 200;
      d.holder.traverse((o) => (o.renderOrder = o.type === "Sprite" ? 400 : order));
    });

    haloMat.opacity = (0.28 + (reduced ? 0 : Math.sin(t * 9) * 0.02 + Math.sin(t * 23) * 0.015)) * ease(it / 1.6);
    if (!reduced) {
      for (const st of steamers) st(dt, t);
      updateEmbers(t);
    }
    renderer.render(scene, camera);
    if (!ready && loadedAt !== null) {
      ready = true;
      opts.onReady();
    }
    raf = requestAnimationFrame(frame);
  }
  function wake() {
    if (raf || disposed || !running || !onScreen) return;
    last = performance.now();
    frame();
  }

  const onVisibility = () => {
    running = !document.hidden;
    wake();
  };
  document.addEventListener("visibilitychange", onVisibility);
  const onContextLost = (e: Event) => {
    e.preventDefault();
    running = false;
    opts.onLost();
  };
  canvas.addEventListener("webglcontextlost", onContextLost);

  resize();
  if (reduced) {
    for (const st of steamers) st(0.5, 1);
    updateEmbers(3);
  }
  frame();

  // The dish arcs from the table into the tray (DOM copy of the same image).
  const proj = new THREE.Vector3();
  function flyToTray(key: DishKey) {
    const d = dishes[DISH_CUTOUTS.indexOf(key)];
    d.holder.getWorldPosition(proj);
    proj.y += d.top * 0.5;
    proj.project(camera);
    const box = canvas.getBoundingClientRect();
    const x = box.left + (proj.x * 0.5 + 0.5) * box.width;
    const y = box.top + (-proj.y * 0.5 + 0.5) * box.height;
    if (x < -100 || x > window.innerWidth + 100 || y < -100 || y > window.innerHeight + 100) return;
    // The tray may only mount after this add — look for it on the next frame.
    requestAnimationFrame(() => {
      if (disposed) return;
      // Desktop cart panel or the mobile tray bar — whichever is actually laid out.
      const r = Array.from(document.querySelectorAll<HTMLElement>("[data-tray-anchor]"))
        .map((el) => el.getBoundingClientRect())
        .find((box) => box.width > 0 && box.height > 0);
      const tx = r ? r.left + r.width * 0.5 : window.innerWidth * 0.5;
      const ty = r ? r.top + r.height * 0.5 : window.innerHeight - 40;
      const img = new Image();
      img.src = `/scene/${key}.webp`;
      img.alt = "";
      img.setAttribute("aria-hidden", "true");
      img.className = "tawa-fly";
      img.style.left = `${x}px`;
      img.style.top = `${y}px`;
      document.body.appendChild(img);
      const dx = tx - x;
      const dy = ty - y;
      const anim = img.animate(
        [
          { transform: "translate(-50%, -50%) scale(1) rotate(0deg)", opacity: 1 },
          {
            transform: `translate(calc(-50% + ${dx * 0.5}px), calc(-50% + ${dy * 0.5 - 160}px)) scale(0.7) rotate(-20deg)`,
            opacity: 1,
            offset: 0.5,
          },
          { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(0.08) rotate(24deg)`, opacity: 0.2 },
        ],
        { duration: 900, easing: "cubic-bezier(0.3, 0.1, 0.3, 1)" },
      );
      anim.onfinish = anim.oncancel = () => img.remove();
    });
  }

  return {
    select(key) {
      const next = DISH_CUTOUTS.indexOf(key);
      if (next < 0) return;
      lastSelect = performance.now();
      // Which slot is at the front right now (the table may have drifted)?
      const n = DISH_CUTOUTS.length;
      const frontNow = ((Math.round(-ringTarget / STEP) % n) + n) % n;
      const turn = shortestTurn(frontNow, next, n);
      ringTarget = -(Math.round(-ringTarget / STEP) + turn) * STEP;
      if (next !== sel && !reduced) dishes[next].flip = 1;
      sel = next;
    },
    hop(key) {
      const k = DISH_CUTOUTS.indexOf(key);
      if (k < 0) return;
      dishes[k].hop = 1;
      if (!reduced && onScreen) flyToTray(key);
    },
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf);
      sizeObserver.disconnect();
      viewObserver.disconnect();
      window.removeEventListener("pointermove", onPointer);
      document.removeEventListener("visibilitychange", onVisibility);
      canvas.removeEventListener("webglcontextlost", onContextLost);
      document.querySelectorAll(".tawa-fly").forEach((n) => n.remove());
      for (const d of disposables) d.dispose();
      renderer.dispose();
    },
  };
}
