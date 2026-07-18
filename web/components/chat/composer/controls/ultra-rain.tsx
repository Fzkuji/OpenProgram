"use client";

/**
 * UltraRain — Effort 最高档滑轨的紫色像素矩阵动画。
 *
 * 核心：紫色与白色一体，共用「单一前沿」。整条轨道是 cols×rows 的格子网格，
 * 每个格子有一个填充度 fill∈[0,1]：0=灰、1=最紫。一道前沿从右往左推进，
 * 扫过的格子 fill 升起。紫底颜色按 fill 在灰↔紫之间插值；白点亮度也乘 fill
 * —— 所以白色扩到哪、紫色就到哪，永远同一个前沿。白点的深浅不一 / 闪烁 /
 * 生灭只是叠在已填充格子上的一层随机亮度扰动，不改变前沿。
 *
 * 前沿推进速度 = 用户说的「扩展速度」，只有一个参数 SPREAD_MS 控制。
 *
 * 性能：逐格 fillRect（不用 per-particle 的 roundRect+path fill，那是之前掉帧
 * 的主因）。每帧两遍网格：紫底一遍、白点一遍，都是矩形填充。
 *
 * 用一个模块级的全局 rAF 循环遍历所有存活 canvas 来画：该 canvas 在
 * Radix Slider 的 Range 里，Slider 频繁重渲染会反复 mount/unmount 组件，
 * 若把 rAF 放进各自的 useEffect 会被 cleanup 掐死（动画整片不动）。全局
 * 循环只认「canvas 是否还挂在文档上」。document.hidden 时暂停。
 */
import { useEffect, useRef } from "react";

const GAP = 1; // 白点方块间隙 —— 露出紫底的细缝
const SPREAD_MS = 2600; // 前沿从右扫到左的时长（= 紫+白一体的扩展速度）
const ROWS = 5;

const easeOut = (p: number) => 1 - Math.pow(1 - p, 3);

function hash(n: number): number {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}

const startAt = new WeakMap<HTMLCanvasElement, number>();
const live = new Set<HTMLCanvasElement>();
let globalRaf = 0;

// 灰 → 中紫。fill=0 灰、fill=1 最紫。
const GRAY: [number, number, number] = [220, 219, 224];
const PURPLE: [number, number, number] = [150, 120, 214];

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

  ctx.clearRect(0, 0, w, h);

  // 网格尺寸：5 排，列距 = 行高（近正方、密排）。
  const rowStep = h / ROWS;
  const colStep = rowStep;
  const cols = Math.ceil(w / colStep);
  const baseW = colStep + 0.5; // +0.5 消抗锯齿细缝，紫底无缝相接
  const baseH = rowStep + 0.5;
  const dotW = Math.max(1, colStep - GAP);
  const dotH = Math.max(1, rowStep - GAP);
  const radius = Math.min(dotW, dotH) * 0.18;

  // ── 单一前沿：从右(px=1)往左(px=0)推进。frontPx 是当前已到达的最左归一
  //    化位置。入场用 easeOut，之后钳在最左边界（紫色最终往左顶到哪）。
  const LEFT_LIMIT = 0.14; // 前沿最终停在 px=0.14（左边留一小段灰，紫不满铺到底）
  const prog = easeOut(Math.min(1, (now - start) / SPREAD_MS)); // 0→1
  const frontPx = 1 - (1 - LEFT_LIMIT) * prog; // 1 → LEFT_LIMIT

  // 每排前沿的小错落：各排到达位置略参差（±约 1 格），固定不晃。
  const rowFrontJit = Array.from({ length: ROWS }, (_, cy) => (hash(cy * 53 + 9) - 0.5) * 0.03);

  // 每个格子的 fill：以「前沿扫过该格的程度」为准。格子中心 px 距离前沿越
  // 远（已被扫过越久）fill 越接近满。加每格随机相位让前沿边缘参差（不是一
  // 条竖直硬线）。fill 决定紫底深浅 + 白点是否可亮 —— 紫白共用它，一体。
  const FEATHER = 0.05; // 前沿羽化宽度（归一化），越小前沿越锐

  // 预生成本帧的行像素起点，减少循环内重复运算。
  for (let cx = 0; cx < cols; cx++) {
    const x = cx * colStep;
    const px = (x + colStep * 0.5) / w; // 格中心归一化 x

    for (let cy = 0; cy < ROWS; cy++) {
      const y = cy * rowStep;
      // 该格随机相位（固定）：让前沿边缘参差、白点各自闪。
      const rndCell = hash(cx * 131 + cy * 17);
      // 逐格填充延迟：前沿不是齐刷刷一条竖线，而是一格格地填 —— 每格在前
      // 沿到达后，还要延迟自己那一小段随机时机才填起来。所以前沿附近会零星
      // 空着、随即很快补上（不是永久空，最终全满）。延迟量越靠左平均略长
      // （左边补得稍慢、临时空格更多），但都会填满。
      // 延迟随入场进度消退（乘 1-prog）：入场时零星空格，prog→1 时归零 →
      // 终态所有格必满，不会永久留空。
      const delay =
        hash(cx * 7.13 + cy * 3.7 + 41) * (0.03 + 0.05 * (1 - px)) * (1 - prog);
      const front = frontPx + rowFrontJit[cy] + (rndCell - 0.5) * 0.02 + delay;
      // px >= front → 已被前沿扫过（在前沿右侧）→ fill 起来；羽化过渡。
      const fill = Math.max(0, Math.min(1, (px - front) / FEATHER + 0.5));
      if (fill <= 0) continue; // 还没轮到这格填（临时空，很快会补）

      // 紫底颜色：灰→紫按 (px 的绝对位置 × fill) 综合。用 px 决定「该到多紫」
      // （越靠右越紫），fill 决定「显现了多少」。二者相乘 → 越靠右越紫、且
      // 只有前沿扫过才显现。左边纯灰段用 pxPurple 压低。
      const pxPurple = Math.max(0, Math.min(1, (px - LEFT_LIMIT) / (1 - LEFT_LIMIT)));
      const shade = pxPurple * fill; // 该格最终紫度
      const pr = GRAY[0] + (PURPLE[0] - GRAY[0]) * shade;
      const pg = GRAY[1] + (PURPLE[1] - GRAY[1]) * shade;
      const pb = GRAY[2] + (PURPLE[2] - GRAY[2]) * shade;
      // 紫底整体不透明度 = fill（前沿羽化处淡入），盖住轨道灰。
      ctx.fillStyle = `rgba(${pr | 0},${pg | 0},${pb | 0},${fill.toFixed(3)})`;
      ctx.fillRect(x, y, baseW, baseH);

      // 白点：叠在已填充格子上的随机亮度扰动。只有在有紫底（shade>0）处才
      // 亮 —— 点必须以紫为底。亮度 = 每格独立相位+频率的呼吸（此起彼伏、
      // 深浅不一），大部分格子中等亮、少数很亮/很暗。乘 fill 使前沿处的白
      // 点跟紫底一起淡入（紫白一体）。
      const phase = rndCell * Math.PI * 2;
      const freq = 2.0 + hash(cx * 131 + cy * 17 + 5) * 3.0; // 2.0~5.0
      const breath = 0.5 + 0.5 * Math.sin(t * freq + phase); // 0~1
      // 向左行进的亮度包络：px*9 + t*2.4，t 一直增长 → 入场结束后紫底不动、
      // 白点仍持续从右往左流动。整条约放 1.5 个亮带（波长够长才看得见移动）。
      // +cy*0.5 让各排不完全竖直同步。0.55~1.0 调制随机闪（不熄灭闪烁本体）。
      const flow = 0.55 + 0.45 * (0.5 + 0.5 * Math.sin(px * 9 + t * 2.4 + cy * 0.5));
      const a = (0.15 + breath * 0.6) * flow * shade * fill;
      if (a > 0.03) {
        ctx.fillStyle = `rgba(255,255,255,${Math.min(1, a).toFixed(3)})`;
        roundRectFill(ctx, x + GAP / 2, y + GAP / 2, dotW, dotH, radius);
      }
    }
  }
}

// 圆角矩形填充（不依赖 ctx.roundRect，兼容性稳）。半径小、格子小，开销可控。
function roundRectFill(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
  ctx.fill();
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
