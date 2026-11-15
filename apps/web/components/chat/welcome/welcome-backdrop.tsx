/**
 * WelcomeBackdrop — the code rain behind the empty chat.
 *
 * Shown only when the "Code rain style" setting is on (off by default,
 * see welcome-backdrop-pref.ts), as dense as that setting says.
 * Reduced-motion draws a single still frame. When the chat gets its first
 * message the rain stops: no new drops start and the scene fades out,
 * while code-rain-arrivals.tsx brings the message in.
 */
"use client";

import { useEffect, useRef } from "react";

import { CODE_RAIN_FONT, codeRainPalette, fillMirrored, mirror, randomGlyph, type CodeRainPalette } from "@/lib/chat/code-rain";
import { useWelcomeBackdropPref } from "@/lib/prefs/appearance/welcome-backdrop-pref";

import styles from "./welcome-screen.module.css";

type Scene = {
  init(width: number, height: number): void;
  /** Percent of the full rain (about 1.35 drops per column at 100). */
  setDensity(percent: number): void;
  /** Stop starting new drops; the ones falling finish. */
  stop(): void;
  draw(ctx: CanvasRenderingContext2D, t: number, width: number, height: number, pal: CodeRainPalette): void;
};

/** Code rain after The Matrix: a fixed grid of mirrored glyphs (katakana,
 *  Latin capitals, digits, symbols) that drops of light run down, a
 *  glowing head with a long green tail. A visible glyph changes at random,
 *  but never again within MIN_GAP seconds of its last change; after that it
 *  rolls once per frame (however many drops light it) so that, gap
 *  included, it changes about every MEAN_GAP seconds. Always green. */
function codeRain(initialDensity: number): Scene {
  const CELL = 20;
  const MEAN_GAP = 10;
  const MIN_GAP = 4;
  const RATE_AFTER_GAP = 1 / (MEAN_GAP - MIN_GAP);
  // rolled: the last frame this glyph was rolled, so two drops over one
  // cell don't roll it twice. changed: when it last changed.
  type Cell = { ch: string; rolled: number; changed: number };
  type Drop = { col: number; y: number; speed: number; len: number };
  let grid: Cell[][] = [];
  let drops: Drop[] = [];
  let rows = 0;
  let cols = 0;
  let density = initialDensity;
  let last = 0;
  let frame = 0;
  let stopped = false;
  const cell = (): Cell => ({ ch: randomGlyph(), rolled: -1, changed: -Infinity });
  // 每滴落到底后换一列重落，稀疏时空出来的列也会轮流下雨。
  const spawn = (scatter: boolean): Drop => {
    const len = 18 + Math.floor(Math.random() * 30);
    return {
      col: Math.floor(Math.random() * cols),
      // 首帧把雨点散在整屏，之后从顶端上方落下。
      y: scatter ? Math.random() * (rows + len) : -Math.random() * rows * 0.6,
      speed: 7 + Math.random() * 12,
      len,
    };
  };
  const target = () => Math.max(1, Math.round(cols * 1.35 * density / 100));
  return {
    init(w, h) {
      last = 0;
      cols = Math.ceil(w / CELL);
      rows = Math.ceil(h / CELL);
      grid = Array.from({ length: cols }, () => Array.from({ length: rows }, cell));
      drops = Array.from({ length: target() }, () => spawn(true));
    },
    setDensity(percent) {
      density = percent;
      if (!cols || stopped) return;
      const want = target();
      if (drops.length > want) drops.length = want;
      while (drops.length < want) drops.push(spawn(true));
    },
    stop() {
      stopped = true;
    },
    draw(ctx, t, _w, _h, pal) {
      const dt = last ? Math.min(0.05, t - last) : 0.016;
      last = t;
      frame++;
      const dark = pal.dark;
      const { tail, head } = pal;
      ctx.font = `600 ${CELL - 3}px ${CODE_RAIN_FONT}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.save();
      mirror(ctx);
      for (let i = drops.length - 1; i >= 0; i--) {
        const d = drops[i];
        d.y += d.speed * dt;
        if (d.y - d.len > rows) {
          if (stopped) { drops.splice(i, 1); continue; }
          Object.assign(d, spawn(false));
        }
        const top = Math.floor(d.y);
        const x = d.col * CELL + CELL / 2;
        for (let k = 0; k < d.len; k++) {
          const row = top - k;
          if (row < 0 || row >= rows) continue;
          const g = grid[d.col][row];
          // 看得见的字每帧掷一次；刚变过的字歇满 MIN_GAP 秒才参与。
          if (g.rolled !== frame) {
            g.rolled = frame;
            if (t - g.changed >= MIN_GAP && Math.random() < dt * RATE_AFTER_GAP) {
              g.ch = randomGlyph();
              g.changed = t;
            }
          }
          const y = row * CELL + CELL / 2;
          if (k === 0) {
            ctx.fillStyle = head;
            ctx.globalAlpha = 1;
            ctx.shadowColor = dark ? tail : "transparent";
            ctx.shadowBlur = dark ? 10 : 0;
          } else {
            ctx.fillStyle = tail;
            // 拖尾前六成保持明亮，最后四成才淡出。
            const fade = Math.min(1, (1 - k / d.len) / 0.4);
            ctx.globalAlpha = Math.max(0.06, fade) * (dark ? 0.92 : 0.78);
            ctx.shadowBlur = 0;
          }
          fillMirrored(ctx, g.ch, x, y);
        }
      }
      ctx.restore();
      ctx.shadowBlur = 0;
    },
  };
}

/** ``stopping``: the chat just got its first message. */
export function WelcomeBackdrop({ stopping = false }: { stopping?: boolean }) {
  const { enabled, density } = useWelcomeBackdropPref();
  return enabled ? <CodeRainCanvas density={density} stopping={stopping} /> : null;
}

function CodeRainCanvas({ density, stopping }: { density: number; stopping: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<Scene | null>(null);
  const densityRef = useRef(density);
  const stillRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (stopping) sceneRef.current?.stop();
  }, [stopping]);

  // 拖滑块时只增删雨滴，不重建画面。
  useEffect(() => {
    densityRef.current = density;
    sceneRef.current?.setDensity(density);
    stillRef.current?.();
  }, [density]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const scene = codeRain(densityRef.current);
    sceneRef.current = scene;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let width = 0, height = 0, frame = 0, raf = 0;
    let palette = codeRainPalette();

    const render = (now: number) => {
      // 主题切换后每秒重读一次 token，不必订阅主题事件。
      if (frame++ % 60 === 0) palette = codeRainPalette();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const dpr = window.devicePixelRatio || 1;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      scene.draw(ctx, now / 1000, width, height, palette);
      ctx.globalAlpha = 1;
      if (!still) raf = requestAnimationFrame(render);
    };

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      width = rect.width;
      height = rect.height;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      scene.init(width, height);
      if (still) render(4000);
    };
    if (still) stillRef.current = () => render(4000);
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();
    if (!still) raf = requestAnimationFrame(render);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(raf);
      sceneRef.current = null;
      stillRef.current = null;
    };
  }, []);

  return <canvas ref={canvasRef} className={stopping ? `${styles.backdrop} ${styles.backdropOut}` : styles.backdrop} aria-hidden="true" />;
}
