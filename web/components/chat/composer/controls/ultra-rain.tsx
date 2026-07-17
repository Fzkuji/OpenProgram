"use client";

/**
 * UltraRain — Effort 最高档滑轨的紫色像素矩阵动画。
 *
 * 固定的格子矩阵（4px 方块 + 1px 间隙），坐标永不移动；每格靠各自的
 * 随机亮灭营造"往左跑"的错觉，而非平移传送带。两条规律：
 *
 * 1. 入场辐射：挂载时整片从右往左逐列点亮（appear ~600ms），一开始
 *    透明、最终稳态。
 * 2. 无规律闪烁：每格独立的正弦相位 + 随机周期决定亮度，相邻格之间
 *    无关联；再叠一层"亮点向左跳"——被选中的格子瞬间拉满再衰减，选中
 *    位置每帧随机，看起来像白点在矩阵里没规律地往左蹦。
 *
 * 底色：左端淡灰、右端明确紫（水平渐变），格子叠在其上。
 */
import { useEffect, useRef } from "react";

const CELL = 4;
const GAP = 1;
const STEP = CELL + GAP; // 5
const RADIATE_MS = 650;

// 一个确定性的伪随机（不依赖 Math.random —— 组件可在任意时刻挂载，
// 用格子索引 + 固定盐产生稳定的每格参数）。
function hash(n: number): number {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
}

export function UltraRain() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    let raf = 0;
    let start = performance.now();
    let cols = 0;
    let rows = 0;

    function resize() {
      const parent = canvas.parentElement;
      if (!parent) return;
      const w = parent.clientWidth;
      const h = parent.clientHeight;
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cols = Math.ceil(w / STEP);
      rows = Math.ceil(h / STEP);
    }

    function frame(now: number) {
      // 先登记下一帧再干活：中途 return（父容器一时不可测/StrictMode
      // 二次挂载）也不会断链，这是之前动画整片空白的根因。
      raf = requestAnimationFrame(frame);
      const parent = canvas.parentElement;
      if (!parent || parent.clientWidth === 0) return;
      const w = parent.clientWidth;
      const h = parent.clientHeight;
      // canvas 尺寸随父容器变化（宽度依档位，动态）——每帧对齐。
      if (canvas.width !== Math.round(w * dpr)) resize();
      const t = (now - start) / 1000;
      // 入场进度 0→1（从右往左点亮：列越靠右越早亮）。
      const radiate = Math.min(1, (now - start) / RADIATE_MS);

      ctx.clearRect(0, 0, w, h);

      // 亮点"向左跳"的当前列 —— 每 ~90ms 换一次随机目标列，制造无规律
      // 往左蹦的白点，而不是匀速滑动。
      const tick = Math.floor((now - start) / 90);

      for (let cx = 0; cx < cols; cx++) {
        // 该列的入场阈值：最右列 threshold≈0 最先亮，最左列≈1 最后亮。
        const colThresh = 1 - cx / Math.max(1, cols - 1);
        const colReveal = Math.max(
          0,
          Math.min(1, (radiate - colThresh) / 0.35 + 0.0001),
        );
        if (colReveal <= 0) continue;

        // 水平位置比例（0 左 → 1 右）决定底/格的紫色浓度。
        const px = (cx * STEP) / Math.max(1, w);

        for (let cy = 0; cy < rows; cy++) {
          const id = cx * 131 + cy * 17;
          const r = hash(id);
          // 稀疏：约 62% 的格子才画（其余是底色）。
          if (r > 0.62) continue;

          // 每格独立闪烁：随机相位 + 随机速度的正弦，映射到 [0.08, 1]。
          const speed = 0.6 + hash(id + 1) * 2.2;
          const phase = hash(id + 2) * Math.PI * 2;
          let br = 0.5 + 0.5 * Math.sin(t * speed + phase);
          br = 0.08 + br * 0.55;

          // 向左跳的白点：若本 tick 随机命中该格，瞬间拉满亮度。
          const jump = hash(tick * 2.3 + cy * 7.7);
          const jumpCol = Math.floor(jump * cols);
          if (cx === jumpCol && hash(id + tick) > 0.55) {
            br = Math.min(1, br + 0.55);
          }

          br *= colReveal;

          // 颜色：左端偏白灰、右端偏紫。用 px 在白与紫之间插值。
          // 白 (245,245,250) → 紫 (142,107,217)
          const rr = Math.round(245 - (245 - 142) * px);
          const gg = Math.round(245 - (245 - 107) * px);
          const bb = Math.round(250 - (250 - 217) * px);
          ctx.fillStyle = `rgba(${rr},${gg},${bb},${br.toFixed(3)})`;
          ctx.beginPath();
          const x = cx * STEP;
          const y = cy * STEP;
          // 圆角小方块。
          const rad = 1;
          ctx.moveTo(x + rad, y);
          ctx.arcTo(x + CELL, y, x + CELL, y + CELL, rad);
          ctx.arcTo(x + CELL, y + CELL, x, y + CELL, rad);
          ctx.arcTo(x, y + CELL, x, y, rad);
          ctx.arcTo(x, y, x + CELL, y, rad);
          ctx.fill();
        }
      }
    }

    resize();
    start = performance.now();
    raf = requestAnimationFrame(frame);
    const ro = new ResizeObserver(resize);
    if (canvas.parentElement) ro.observe(canvas.parentElement);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      style={{ position: "absolute", inset: 0, pointerEvents: "none" }}
    />
  );
}
