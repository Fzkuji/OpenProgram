"use client";

/**
 * UltraRain — Effort 最高档滑轨的紫色像素矩阵动画。
 *
 * 满铺的白色小方块矩阵盖在紫色渐变底上（底左灰白→右深紫）。每个方块
 * 的白色亮度由一道从右往左行进的波 + 各自随机相位决定：波峰扫到的方块
 * 泛白凸出，其余融进紫底 —— 肉眼看是白色亮块成群地从右往左流动。入场
 * 时整条从右往左点亮。
 *
 * 用一个模块级的全局 rAF 循环遍历所有存活 canvas 来画：该 canvas 在
 * Radix Slider 的 Range 里，Slider 频繁重渲染会反复 mount/unmount 组件，
 * 若把 rAF 放进各自的 useEffect 会被 cleanup 掐死（动画整片不动）。全局
 * 循环只认「canvas 是否还挂在文档上」。document.hidden 时暂停。
 */
import { useEffect, useRef } from "react";

// 固定 5 排：行高/列距由轨道尺寸在 paint 里反推，格子小、密排。
const GAP = 1; // 方块间隙 —— 露出紫底的细缝
const RADIATE_MS = 1100; // 入场时长

const easeOut = (p: number) => 1 - Math.pow(1 - p, 3);

function hash(n: number): number {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}

const startAt = new WeakMap<HTMLCanvasElement, number>();
const live = new Set<HTMLCanvasElement>();
let globalRaf = 0;

function paint(canvas: HTMLCanvasElement, now: number) {
  const parent = canvas.parentElement;
  if (!parent) return;
  const dpr = window.devicePixelRatio || 1;
  const w = parent.clientWidth;
  const h = parent.clientHeight;
  if (w === 0 || h === 0) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  let start = startAt.get(canvas);
  if (start == null) {
    start = now;
    startAt.set(canvas, now);
  }
  const t = (now - start) / 1000;
  const radiate = easeOut(Math.min(1, (now - start) / RADIATE_MS));
  // 入场已铺到的最左 x（右端先亮，往左推进的前沿）。
  const frontX = w * (1 - radiate);

  ctx.clearRect(0, 0, w, h);

  // 固定 5 排（用户要求）。行高 = (轨道高 − 上下微边距) / 5；格子占行高
  // 的大部分、留细缝。列按同一格距水平铺满。
  const PAD = 2;
  const usableH = h - PAD * 2;
  const rows = 5;
  const rowStep = usableH / rows; // 每行占的高度（含缝）
  const cellH = Math.max(1, rowStep - GAP); // 方块高
  const offY = PAD;
  // 列用和行相近的格距，让方块接近正方、密排。
  const colStep = rowStep;
  const cellW = Math.max(1, colStep - GAP);
  const cols = Math.ceil(w / colStep);

  // 底色目标：左灰白 → 右深紫（按水平位置 px）。
  function baseColor(px: number): [number, number, number] {
    // 灰白 (228,226,234) → 深紫 (122,86,204)，中段偏亮紫。
    const p = Math.max(0, Math.min(1, (px - 0.05) / 0.95));
    const r = 228 - (228 - 122) * p;
    const g = 226 - (226 - 86) * p;
    const b = 234 - (234 - 204) * p;
    return [r, g, b];
  }

  // ── 逐列绘制：紫色靠一列一列铺开（列已入场则底色 = 平滑紫）；
  //    推进前沿附近有一条方块带（方块随前沿往左移），前沿扫过后方块
  //    淡出、只剩平滑紫。前沿宽度 = 若干列的过渡。
  const FRONT_W = w * 0.22; // 前沿方块带的宽度
  for (let cx = 0; cx < cols; cx++) {
    const x = cx * colStep;
    const px = x / Math.max(1, w);
    // distFront>0 表示在前沿右侧（已铺）。
    const distFront = x - frontX;
    if (distFront < -colStep) continue; // 前沿左侧还没到

    const [bR, bG, bB] = baseColor(px);

    // 底色淡入：刚被前沿扫到时从 0 起、越往右越实。
    const baseIn = Math.max(0, Math.min(1, (distFront + colStep) / (colStep * 2)));

    // 方块带强度：前沿附近明显、向右淡出；入场结束后整条常驻流动波。
    const frontFactor = radiate < 1 ? Math.max(0, 1 - distFront / FRONT_W) : 1;

    for (let cy = 0; cy < rows; cy++) {
      const y = offY + cy * rowStep;
      // 底色方块（平滑紫，格间留缝）。
      ctx.fillStyle = `rgba(${bR|0},${bG|0},${bB|0},${baseIn.toFixed(3)})`;
      ctx.fillRect(x, y, cellW, cellH);

      // 白亮波：从右往左流动（相位 +cx −t），乘 frontFactor → 前沿最亮。
      const id = cx * 131 + cy * 17;
      const indiv = hash(id) * Math.PI * 2;
      const wave = Math.pow(0.5 + 0.5 * Math.sin(cx * 0.5 - t * 3.0 + indiv), 2.0);
      const a = wave * 0.85 * frontFactor * baseIn;
      if (a > 0.02) {
        ctx.fillStyle = `rgba(255,255,255,${a.toFixed(3)})`;
        ctx.fillRect(x, y, cellW, cellH);
      }
    }
  }
}

function tick(now: number) {
  for (const canvas of live) {
    if (!canvas.isConnected) {
      live.delete(canvas);
      continue;
    }
    if (!document.hidden) paint(canvas, now);
  }
  globalRaf = live.size > 0 ? requestAnimationFrame(tick) : 0;
}

function register(canvas: HTMLCanvasElement) {
  live.add(canvas);
  if (globalRaf === 0) globalRaf = requestAnimationFrame(tick);
}

function unregister(canvas: HTMLCanvasElement) {
  live.delete(canvas);
  startAt.delete(canvas);
}

export function UltraRain() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    startAt.delete(canvas); // 重新进入 max = 重播入场
    register(canvas);
    return () => unregister(canvas);
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
    />
  );
}
