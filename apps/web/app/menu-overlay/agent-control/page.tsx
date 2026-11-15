"use client";

/**
 * The control overlay over a desktop app window an Agent operates, drawn in
 * a transparent click-through window the desktop shell lays over it with a
 * margin (apps/desktop/main/worker/agent-control.js). A web page the Agent
 * operates gets the same overlay over its native view
 * (apps/desktop/main/windows/action-cues.js), without the lights:
 *
 * - an accent glow along the inside of the window's edge that breathes while
 *   the Agent holds it;
 * - the window's close, minimize and zoom lights in the accent, pulsing in turn;
 * - the agent cursor, which stays in the window between steps and glides
 *   along a gentle arc to the next one, pressing where it clicks.
 *
 * Windows in front of the controlled one are cut out of the glow and lights,
 * and the cursor hides while the point it goes to lies under one of them.
 */

import { Suspense, useEffect, useRef, useState } from "react";

import { AgentCursor } from "@/components/agent-cursor/agent-cursor";
import { coveredPoint } from "@/components/agent-cursor/covered-point";
import styles from "./agent-control.module.css";

interface ControlUpdate {
  margin: number;
  width: number;
  height: number;
  appName: string;
  buttons: number[] | null;
  occluders: number[][];
  ending?: boolean;
  reducedMotion?: boolean;
  /** False while the cursor shows without the Agent holding the page. */
  glow?: boolean;
  /** The corner radius of what the glow follows; a macOS window's by default. */
  radius?: number;
  /** Relative to the window's top left. */
  cursor: { x: number; y: number; action: string; seq: number } | null;
}

interface ControlBridge {
  ready(): void;
  onUpdate(cb: (payload: ControlUpdate) => void): () => void;
}

function controlBridge(): ControlBridge | null {
  return (window as unknown as { openprogramDesktop?: { agentControlOverlay?: ControlBridge } })
    .openprogramDesktop?.agentControlOverlay ?? null;
}

// The corner of a macOS window, which the glow follows.
const WINDOW_RADIUS = 10;
const CURSOR_BOX = 40;
// Steps that press something play the press and ripple when the cursor lands.
const PRESSES = /click|press(?!_key)|select|drag|toggle|perform|open/;

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

function AgentControlPage() {
  const [update, setUpdate] = useState<ControlUpdate | null>(null);
  const [press, setPress] = useState(0);
  // The App's interface zoom reaches this page too; geometry comes in
  // screen points, so the stage is scaled back to points.
  const [viewportWidth, setViewportWidth] = useState(() => (typeof window === "undefined" ? 0 : window.innerWidth));
  const cursorRef = useRef<HTMLDivElement>(null);
  // Where the cursor's tip is now, in overlay coordinates.
  const at = useRef<{ x: number; y: number } | null>(null);
  const shownSeq = useRef<number | null>(null);
  const frame = useRef(0);

  useEffect(() => {
    document.documentElement.style.background = "transparent";
    document.body.style.background = "transparent";
    const api = controlBridge();
    if (!api) return;
    const resize = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", resize);
    const stop = api.onUpdate(setUpdate);
    api.ready();
    return () => { stop(); window.removeEventListener("resize", resize); };
  }, []);

  // Glide to each new step; follow the window when it moves.
  useEffect(() => {
    const node = cursorRef.current;
    if (!update || !node) return;
    const { margin, cursor, reducedMotion } = update;
    if (!cursor) return;
    const target = { x: margin + cursor.x, y: margin + cursor.y };
    const place = (p: { x: number; y: number }) => {
      at.current = p;
      node.style.transform = `translate(${p.x - CURSOR_BOX / 2}px, ${p.y - CURSOR_BOX / 2}px)`;
    };
    if (shownSeq.current === cursor.seq) {
      // Same step, the window moved: the cursor moves with it.
      cancelAnimationFrame(frame.current);
      place(target);
      return;
    }
    shownSeq.current = cursor.seq;
    const land = () => { if (PRESSES.test(cursor.action)) setPress(cursor.seq); };
    // The first step starts from the middle of the window.
    const start = at.current ?? { x: update.width / 2, y: update.height / 2 };
    const dx = target.x - start.x;
    const dy = target.y - start.y;
    const distance = Math.hypot(dx, dy);
    cancelAnimationFrame(frame.current);
    if (reducedMotion || distance < 2) {
      place(target);
      land();
      return;
    }
    // A gentle arc: the control point sits off the straight line, to its left.
    const bend = Math.min(distance * 0.16, 56);
    const control = { x: start.x + dx / 2 + (dy / distance) * bend, y: start.y + dy / 2 - (dx / distance) * bend };
    const duration = Math.min(680, Math.max(240, 200 + distance * 0.7));
    const began = performance.now();
    const step = (now: number) => {
      const t = easeInOut(Math.min(1, (now - began) / duration));
      const u = 1 - t;
      place({
        x: u * u * start.x + 2 * u * t * control.x + t * t * target.x,
        y: u * u * start.y + 2 * u * t * control.y + t * t * target.y,
      });
      if (t < 1) frame.current = requestAnimationFrame(step);
      else land();
    };
    frame.current = requestAnimationFrame(step);
  }, [update]);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  if (!update) return null;
  const { margin, width, height, buttons, occluders, ending, reducedMotion } = update;
  const radius = update.radius ?? WINDOW_RADIUS;
  const cursorShown = update.cursor !== null && !coveredPoint(occluders, margin + update.cursor.x, margin + update.cursor.y);
  const w = width - 2 * margin;
  const h = height - 2 * margin;
  // The title-bar lights: three evenly spaced circles across the buttons' frame.
  const lights = buttons ? (() => {
    const [bx, by, bw, bh] = buttons;
    const r = bh / 2;
    return [0, 1, 2].map(i => ({ cx: bx + r + (i * (bw - bh)) / 2, cy: by + r, r: r + 0.5 }));
  })() : [];

  return (
    <div className={styles.stage} data-ending={ending ? "true" : undefined} data-reduced-motion={reducedMotion ? "true" : undefined}
      style={{ width, height, transform: viewportWidth && width ? `scale(${viewportWidth / width})` : undefined }}>
      <svg className={styles.layer} width={width} height={height}>
        <defs>
          <mask id="agent-control-uncovered" maskUnits="userSpaceOnUse" x={0} y={0} width={width} height={height}>
            <rect x={0} y={0} width={width} height={height} fill="#fff" />
            {occluders.map(([x, y, ow, oh], i) => <rect key={i} x={x} y={y} width={ow} height={oh} fill="#000" />)}
          </mask>
          {/* The glow lives inside the window and fades toward its middle. */}
          <clipPath id="agent-control-window">
            <rect x={margin} y={margin} width={w} height={h} rx={radius} />
          </clipPath>
          <filter id="agent-control-glow" x="-10%" y="-10%" width="120%" height="120%">
            <feGaussianBlur stdDeviation={11} />
          </filter>
        </defs>
        <g mask="url(#agent-control-uncovered)">
          {update.glow === false ? null : (
            <g className={styles.breathe} clipPath="url(#agent-control-window)">
              <rect className={styles.glow} x={margin} y={margin} width={w} height={h} rx={radius}
                filter="url(#agent-control-glow)" />
            </g>
          )}
          {lights.length ? (
            <g data-agent-control-lights="true">
              {lights.map((light, i) => (
                <g key={i}>
                  <circle className={styles.light} cx={light.cx} cy={light.cy} r={light.r} />
                  <circle className={styles.lightPulse} cx={light.cx} cy={light.cy} r={light.r}
                    style={{ animationDelay: `${i * 0.2}s` }} />
                </g>
              ))}
            </g>
          ) : null}
        </g>
      </svg>
      <div ref={cursorRef} className={styles.cursor} style={{ opacity: cursorShown ? 1 : 0 }}>
        <AgentCursor play={press && !reducedMotion ? "once" : "never"} playSeq={press} />
      </div>
    </div>
  );
}

export default function Page() {
  return (
    <Suspense>
      <AgentControlPage />
    </Suspense>
  );
}
