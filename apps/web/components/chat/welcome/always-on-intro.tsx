/**
 * AlwaysOnIntro — ALWAYS-ON spelled in glyphs, once, in an empty chat whose
 * Agent is always on.
 *
 * Glyphs fly in from across the empty area and settle into the word, hold,
 * then scatter and fade; after that the chat is as empty as any other.
 * It plays only when the chat switches to an always-on Agent
 * (welcome-screen.tsx decides). Reduced-motion shows the word still and fades it.
 * Colours run from the theme's cyan to its purple; on a light background
 * the glyphs are darker, bolder and fully opaque so the word reads as
 * clearly as on a dark one. The word sits in the middle of the empty area.
 */
"use client";

import { useEffect, useRef, useState } from "react";

import { CODE_RAIN_FONT } from "@/lib/chat/code-rain";

const WORD = "ALWAYS-ON";
const GLYPHS = "0123456789:=+*#%<>|-";
const SETTLED = ":=+*#";
const IN_END = 1.2;
const HOLD_END = 2.6;
const OUT_END = 3.4;

type Glyph = { sx: number; sy: number; tx: number; ty: number; ex: number; ey: number; delay: number; ch: string; mix: number };

const pick = (chars: string) => chars[Math.floor(Math.random() * chars.length)];
const ease = (k: number) => 1 - Math.pow(1 - k, 3);

/** A theme token as a colour the canvas can draw with. */
function tokenColor(host: HTMLElement, token: string): string {
  const probe = document.createElement("span");
  probe.style.color = `var(${token})`;
  host.appendChild(probe);
  const color = getComputedStyle(probe).color;
  probe.remove();
  return color;
}

function channels(color: string): number[] {
  return (color.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
}

/** True when the page behind the chat is light. */
function onLight(): boolean {
  const [r = 0, g = 0, b = 0] = channels(getComputedStyle(document.body).backgroundColor);
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.6;
}

/** The colour pulled toward black, so a pale accent holds up on a light page. */
function deepen(color: string, by: number): string {
  const [r = 0, g = 0, b = 0] = channels(color);
  return `rgb(${Math.round(r * by)}, ${Math.round(g * by)}, ${Math.round(b * by)})`;
}

/** Where each glyph of the word sits: the word drawn off-screen and sampled on a grid. */
function wordCells(width: number, height: number, size: number, step: number): Array<{ x: number; y: number }> {
  const off = document.createElement("canvas");
  off.width = Math.ceil(width);
  off.height = Math.ceil(size * 1.4);
  const ctx = off.getContext("2d");
  if (!ctx) return [];
  ctx.fillStyle = "#fff";
  ctx.font = `800 ${size}px Inter, system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(WORD, off.width / 2, off.height / 2);
  const data = ctx.getImageData(0, 0, off.width, off.height).data;
  const top = height / 2 - off.height / 2;
  const cells: Array<{ x: number; y: number }> = [];
  for (let y = 0; y < off.height; y += step) {
    for (let x = 0; x < off.width; x += step) {
      if (data[(y * off.width + x) * 4 + 3] > 110) cells.push({ x, y: top + y });
    }
  }
  return cells;
}

export function AlwaysOnIntro() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    const box = canvas?.parentElement;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !box || !ctx) return;
    const width = box.clientWidth;
    const height = box.clientHeight;
    if (!width || !height) {
      setDone(true);
      return;
    }
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    ctx.scale(ratio, ratio);
    const light = onLight();
    const from = light ? deepen(tokenColor(box, "--accent-cyan"), 0.72) : tokenColor(box, "--accent-cyan");
    const to = light ? deepen(tokenColor(box, "--accent-purple"), 0.85) : tokenColor(box, "--accent-purple");
    const size = Math.min(110, width / 6.5);
    const step = Math.max(4, Math.round(size / 18));
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const glyphs: Glyph[] = wordCells(width, height, size, step).map((cell) => ({
      sx: Math.random() * width, sy: Math.random() * height,
      tx: cell.x, ty: cell.y,
      ex: cell.x + (Math.random() - 0.5) * width * 0.6, ey: cell.y + (Math.random() - 0.5) * height * 0.5,
      delay: still ? 0 : Math.random() * 0.5,
      ch: still ? pick(SETTLED) : pick(GLYPHS),
      mix: width ? cell.x / width : 0,
    }));
    const font = `${light ? 700 : 400} ${Math.max(7, step + 2)}px ${CODE_RAIN_FONT}`;
    let raf = 0;
    const start = performance.now();
    const draw = (now: number) => {
      const t = (now - start) / 1000;
      ctx.clearRect(0, 0, width, height);
      if (t >= OUT_END) {
        setDone(true);
        return;
      }
      ctx.font = font;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const glyph of glyphs) {
        let x = glyph.tx;
        let y = glyph.ty;
        let alpha = 1;
        let settled = true;
        if (still) {
          alpha = t < HOLD_END ? Math.min(1, t / 0.3) : 1 - (t - HOLD_END) / (OUT_END - HOLD_END);
        } else if (t < IN_END) {
          const k = ease(Math.min(1, Math.max(0, (t - glyph.delay) / 0.7)));
          x = glyph.sx + (glyph.tx - glyph.sx) * k;
          y = glyph.sy + (glyph.ty - glyph.sy) * k;
          alpha = k;
          settled = k >= 1;
        } else if (t >= HOLD_END) {
          const k = ease(Math.min(1, (t - HOLD_END) / (OUT_END - HOLD_END)));
          x = glyph.tx + (glyph.ex - glyph.tx) * k;
          y = glyph.ty + (glyph.ey - glyph.ty) * k;
          alpha = 1 - k;
        }
        if (!still && Math.random() < (settled ? 0.02 : 0.2)) glyph.ch = settled ? pick(SETTLED) : pick(GLYPHS);
        ctx.globalAlpha = Math.max(0, alpha) * (light ? 1 : 0.55 + 0.45 * (1 - Math.abs(glyph.mix - 0.5)));
        ctx.fillStyle = glyph.mix < 0.5 ? from : to;
        if (glyph.mix > 0.35 && glyph.mix < 0.65 && Math.random() < 0.5) ctx.fillStyle = glyph.mix < 0.5 ? to : from;
        ctx.fillText(glyph.ch, x, y);
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(draw);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  if (done) return null;
  return <canvas ref={canvasRef} aria-hidden className="pointer-events-none absolute inset-0 h-full w-full" />;
}
