"use client";

/**
 * One Bot character: a coloured shape with two eyes.
 *
 * At rest it is a still picture. With `live` it blinks, sways and looks
 * around on its own: ./wander gives each character its own glances, which
 * turn its eyes and body. `mood` shows what the Bot is doing. It does not
 * follow the pointer: `followPointer` stays off, which also keeps the
 * renderer from measuring the character every frame.
 * When the system asks for reduced motion a live character is the still
 * picture too. The renderer pauses off screen and shares one animation
 * loop between every character on the page.
 *
 * The drawing box is scaled and placed so the body fills its slot about
 * as much as an icon fills the same slot (botFit in ./catalog), and the
 * slot pops on its control's hover like every icon (@/components/ui/hover-pop).
 *
 * The renderer's first drawing of a shape is costly, so shapes are
 * prepared one per task (./warm). Until its shape is ready, and on the
 * server, a character is its flat glyph in its own colours.
 */
import { createElement, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
import { motion, useAnimation } from "framer-motion";
import type { MountedAvatar } from "open-bot-avatar/dom";

import { HOVER_POP, HOVER_POP_FILL, HOVER_SPRING, followHostHover } from "../../ui/hover-pop";

import { BOT_COLORS, BOT_EYE_COLOR, DEFAULT_BOT_COLOR, botFit, botMarkFit, isBotColor } from "./catalog";
import { botGlyph } from "./glyph";
import { loadBotRuntime, loadedBotRuntime } from "./runtime";
import { shapeReady, startWarming, wantShape, watchShapes } from "./warm";
import { startWander } from "./wander";

export type BotMood = "idle" | "thinking" | "happy";

export interface BotAvatarProps {
  shape: string;
  color: string;
  size: number;
  /** Animate. Off, the character is one still picture. */
  live?: boolean;
  mood?: BotMood;
  /** Accessible name; leave empty where the name is written beside it. */
  label?: string;
  className?: string;
  /** How the body fills its slot: "avatar" (botFit, the default, used
   *  everywhere else) or "mark" (botMarkFit), only for the composer's
   *  Agent chip and the Agent menu: the body's longer side fills the slot
   *  and the slot grows to exactly 18 px on hover (HOVER_POP_FILL). */
  fit?: BotFitRule;
}

export type BotFitRule = "avatar" | "mark";

const useBrowserLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";
const reducedMotionQuery = () => (typeof matchMedia === "undefined" ? null : matchMedia(REDUCED_MOTION));

function watchReducedMotion(onChange: () => void): () => void {
  const query = reducedMotionQuery();
  query?.addEventListener("change", onChange);
  return () => query?.removeEventListener("change", onChange);
}

/** Whether the system asks for reduced motion; false on the server. */
function useReducedMotion(): boolean {
  return useSyncExternalStore(watchReducedMotion, () => reducedMotionQuery()?.matches ?? false, () => false);
}

export function BotAvatar({ shape, color, size, live = false, mood = "idle", label = "", className, fit: fitRule = "avatar" }: BotAvatarProps) {
  const slot = useRef<HTMLSpanElement | null>(null);
  const host = useRef<HTMLSpanElement | null>(null);
  const pop = useAnimation();
  const mounted = useRef<MountedAvatar | null>(null);
  const [runtime, setRuntime] = useState(loadedBotRuntime);

  useEffect(() => {
    if (runtime) return;
    let disposed = false;
    void loadBotRuntime().then((next) => { if (!disposed) setRuntime(next); }).catch(() => {});
    return () => { disposed = true; };
  }, [runtime]);

  const hex = BOT_COLORS[isBotColor(color) ? color : DEFAULT_BOT_COLOR];
  const prepared = useSyncExternalStore(watchShapes, () => shapeReady(shape), () => false);
  const drawn = !!runtime && prepared;

  useEffect(() => {
    if (!prepared) wantShape(shape);
    if (runtime) startWarming(runtime);
  }, [runtime, prepared, shape]);
  const reduced = useReducedMotion();
  const moving = live && !reduced;

  useBrowserLayoutEffect(() => {
    const element = host.current;
    if (!runtime || !element || !drawn) return;
    const options = {
      definition: runtime.definition(shape),
      color: hex,
      eyeColor: BOT_EYE_COLOR,
      mood,
      still: !moving,
      followPointer: false,
      label,
    };
    if (mounted.current) mounted.current.update(options);
    else mounted.current = runtime.mountAvatar(element, options);
  }, [runtime, drawn, shape, hex, mood, moving, label]);

  // Glances only while idle: a working or happy character has its own pose.
  const wandering = moving && mood === "idle";
  useEffect(() => {
    if (!drawn || !wandering) return;
    const stop = startWander((lookAt) => mounted.current?.update({ lookAt }));
    return () => {
      stop();
      mounted.current?.update({ lookAt: null });
    };
  }, [drawn, wandering]);

  useEffect(() => {
    const element = slot.current;
    if (!element || reduced) return;
    return followHostHover(element, () => void pop.start("animate"), () => void pop.start("normal"));
  }, [pop, reduced]);

  useEffect(() => () => {
    mounted.current?.destroy();
    mounted.current = null;
  }, []);

  const fit = fitRule === "mark" ? botMarkFit(shape) : botFit(shape);
  const style: CSSProperties = {
    position: "relative",
    width: size,
    height: size,
    flexShrink: 0,
    display: "inline-block",
    verticalAlign: "middle",
  };
  // The renderer draws into the body's own box, larger than the slot, so a
  // live canvas is sized for what shows instead of being scaled up.
  const bodyStyle: CSSProperties = {
    position: "absolute",
    left: fit.left * size,
    top: fit.top * size,
    width: fit.scale * size,
    height: fit.scale * size,
  };
  // Data attributes are not in motion's prop types; they pass through to the span.
  const slotProps = {
    ref: slot,
    className,
    style,
    initial: "normal",
    animate: pop,
    variants: fitRule === "mark" ? HOVER_POP_FILL : HOVER_POP,
    transition: HOVER_SPRING,
    "data-bot-shape": shape,
    "data-bot-live": live ? "" : undefined,
  } as Parameters<typeof motion.span>[0];
  return createElement(
    motion.span,
    slotProps,
    createElement("span", { ref: host, style: bodyStyle, "data-bot-body": "" }),
    drawn ? null : standIn(shape, hex, bodyStyle),
  );
}

/** The character while its shape is being prepared: the flat glyph in its
 *  colours, in the box the renderer will draw into. */
function standIn(shape: string, hex: string, box: CSSProperties) {
  const glyph = botGlyph(shape);
  return createElement(
    "svg",
    { viewBox: "0 0 100 100", style: { ...box, overflow: "visible" }, "aria-hidden": "true", "data-bot-stand-in": "" },
    createElement("path", { d: glyph.body, fill: hex }),
    createElement("path", { d: glyph.eyes, fill: BOT_EYE_COLOR }),
  );
}
