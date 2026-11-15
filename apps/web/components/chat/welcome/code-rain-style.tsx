"use client";

/**
 * The code rain style's in-progress pieces (styles in code-rain-style.css):
 * with the style on, anything still running is shown as code. A spinner
 * becomes a few columns of falling glyphs (CodeRainIcon), and a status
 * label becomes a green monospace line (CodeRainText) whose characters
 * rain in like a new message's (code-rain-arrivals.tsx). Under reduced
 * motion both hold still.
 */
import { useEffect, useRef } from "react";

import { CODE_RAIN_FONT, codeRainPalette, fillMirrored, mirror, randomGlyph } from "@/lib/chat/code-rain";
import { useWelcomeBackdropPref } from "@/lib/prefs/appearance/welcome-backdrop-pref";

/** Whether the code rain style is on. */
export function useCodeRainStyle(): boolean {
  return useWelcomeBackdropPref().enabled;
}

/** Three columns of falling glyphs in a square ``size`` px wide. */
export function CodeRainIcon({ size = 18, className = "" }: { size?: number; className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const step = size / 3, tail = 4;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(size * dpr);
    canvas.height = Math.round(size * dpr);
    // Staggered heads, so the square is never empty.
    const columns = [0, 1, 2].map((i) => ({
      x: step * (i + 0.5),
      head: (i * size) / 3 + Math.random() * step,
      v: size * (1.8 + Math.random() * 1.4),
      glyphs: Array.from({ length: tail }, randomGlyph),
    }));
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let last = performance.now();
    const draw = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      const pal = codeRainPalette();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size, size);
      ctx.font = `700 ${Math.round(step * 1.15)}px ${CODE_RAIN_FONT}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      mirror(ctx);
      for (const c of columns) {
        c.head += c.v * dt;
        if (c.head - (tail - 1) * step > size) { c.head = 0; c.v = size * (1.8 + Math.random() * 1.4); }
        for (let k = 0; k < tail; k++) {
          const y = Math.floor(c.head / step) * step - k * step + step / 2;
          if (y < 0 || y > size) continue;
          if (Math.random() < 0.04) c.glyphs[k] = randomGlyph();
          ctx.globalAlpha = k === 0 ? 1 : 1 - k / tail;
          ctx.fillStyle = k === 0 ? pal.head : pal.tail;
          fillMirrored(ctx, c.glyphs[k], c.x, y);
        }
      }
      ctx.globalAlpha = 1;
      if (!still) raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [size]);
  return (
    <span className={`code-rain-icon ${className}`.trim()} style={{ width: size, height: size }} aria-hidden="true">
      <canvas ref={ref} />
    </span>
  );
}

/** A green monospace label. Its characters rain in, and now and then one
 *  flickers back into code, through code-rain-arrivals.tsx; the DOM text
 *  itself never changes, so screen readers read it as it is. */
export function CodeRainText({ text, className = "" }: { text: string; className?: string }) {
  return <span className={`code-rain-text ${className}`.trim()}>{text}</span>;
}
