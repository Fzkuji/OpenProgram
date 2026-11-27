"use client";

/**
 * The composer's fill rule for a drawing SolarIcon and BrandMark do not
 * draw themselves: a lucide icon, a hand-drawn svg, a file-type icon, an
 * app icon or a favicon in the composer or a popup it opens.
 *
 * The box is 16 x 16 (FILL_BOX). An svg inside it is cropped to its ink:
 * its view box becomes the square around the painted part (the geometry's
 * bounding box plus half the stroke), so the longer side of the ink fills
 * the box, centred. An image or canvas is fitted into the box whole. On its
 * control's hover the box grows to exactly 18 x 18 (HOVER_POP_FILL), the
 * same motion as a filled Solar glyph.
 *
 * Opt-in, used only at the sites the fill rule covers: the composer and the
 * popups it opens. Every other icon, including the question and approval
 * cards drawn under a reply, keeps its own drawing and the shared 1.12 pop.
 */
import { useEffect, useLayoutEffect, useRef, type CSSProperties, type ReactNode } from "react";
import { motion, useAnimation, useReducedMotion } from "framer-motion";

import { cn } from "@/lib/utils";
import { FILL_BOX, HOVER_POP_FILL, HOVER_SPRING, followHostHover } from "@/components/ui/hover-pop";

const useBrowserLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** The square view box around an svg's ink, or null when it cannot be
 *  measured (not rendered, or no layout engine). */
export function inkViewBox(svg: SVGSVGElement): string | null {
  let box: DOMRect | SVGRect;
  try {
    box = svg.getBBox();
  } catch {
    return null;
  }
  if (!box || !(box.width > 0 || box.height > 0)) return null;
  // Half the widest stroke of any stroked shape lies outside the geometry.
  let pad = 0;
  if (typeof getComputedStyle === "function") {
    for (const shape of Array.from(svg.querySelectorAll("path, circle, ellipse, line, polyline, polygon, rect"))) {
      const computed = getComputedStyle(shape);
      if (!computed.stroke || computed.stroke === "none") continue;
      pad = Math.max(pad, (parseFloat(computed.strokeWidth) || 0) / 2);
    }
  }
  const width = box.width + 2 * pad;
  const height = box.height + 2 * pad;
  const side = Math.max(width, height);
  const round = (value: number) => Math.round(value * 1000) / 1000;
  const x = box.x - pad + width / 2 - side / 2;
  const y = box.y - pad + height / 2 - side / 2;
  return [round(x), round(y), round(side), round(side)].join(" ");
}

/** Ink boxes by drawing: the svg's class, stroke width and markup. The
 *  same glyph has the same ink wherever it is drawn. Measuring forces the
 *  page's style and layout in the middle of a commit (getBBox), and the fit
 *  runs after every render of every composer icon: an effort change or a
 *  form opening paid it a dozen times over (traced). */
const inkBoxes = new Map<string, string>();
const INK_BOXES_MAX = 500;

function cachedInkViewBox(svg: SVGSVGElement): string | null {
  const key = `${svg.getAttribute("class") ?? ""}|${svg.getAttribute("stroke-width") ?? ""}|${svg.innerHTML}`;
  const known = inkBoxes.get(key);
  if (known) return known;
  const measured = inkViewBox(svg);
  if (!measured) return null;
  if (inkBoxes.size >= INK_BOXES_MAX) inkBoxes.clear();
  inkBoxes.set(key, measured);
  return measured;
}

/** Fit the drawing inside `box` to a `size` px square. */
function fitDrawing(box: HTMLElement, size: number, crop: boolean) {
  const drawing = box.querySelector("svg, img, canvas");
  if (!drawing) return;
  const element = drawing as HTMLElement | SVGSVGElement;
  if (drawing instanceof Element && drawing.tagName.toLowerCase() === "svg") {
    if (!crop) return;
    const svg = drawing as SVGSVGElement;
    const viewBox = cachedInkViewBox(svg);
    if (!viewBox) return;
    if (svg.getAttribute("viewBox") !== viewBox) svg.setAttribute("viewBox", viewBox);
    if (svg.style.overflow !== "visible") svg.style.overflow = "visible";
  } else if ((element as HTMLElement).style.objectFit !== "contain") {
    (element as HTMLElement).style.objectFit = "contain";
  }
  // Written only when they differ: an inline style write restyles the
  // drawing even when the value is the same.
  const side = `${size}px`;
  if (element.style.width !== side) element.style.width = side;
  if (element.style.height !== side) element.style.height = side;
  if (element.style.flexShrink !== "0") element.style.flexShrink = "0";
}

export interface FillIconProps {
  children: ReactNode;
  /** The box's side in px. */
  size?: number;
  /** Crop an svg to its ink (true) or leave a drawing that already fills
   *  its box as it is (false: the context ring). */
  crop?: boolean;
  className?: string;
  style?: CSSProperties;
}

export function FillIcon({ children, size = FILL_BOX, crop = true, className, style }: FillIconProps) {
  const self = useRef<HTMLSpanElement>(null);
  const pop = useAnimation();
  const reduced = useReducedMotion();

  useEffect(() => {
    const element = self.current;
    if (!element || reduced) return;
    return followHostHover(element, () => void pop.start("animate"), () => void pop.start("normal"));
  }, [pop, reduced]);

  // Refit whenever the drawing changes (a favicon replacing its stand-in,
  // a glyph switching): the observer catches changes made by children.
  useBrowserLayoutEffect(() => {
    const element = self.current;
    if (!element) return;
    fitDrawing(element, size, crop);
    if (typeof MutationObserver === "undefined") return;
    const watcher = new MutationObserver(() => fitDrawing(element, size, crop));
    watcher.observe(element, { childList: true, subtree: true });
    return () => watcher.disconnect();
  });

  return (
    <motion.span
      ref={self}
      aria-hidden="true"
      data-icon-fit="fill"
      className={cn("inline-flex shrink-0 items-center justify-center", className)}
      style={{ width: size, height: size, ...style }}
      variants={HOVER_POP_FILL}
      initial="normal"
      animate={pop}
      transition={HOVER_SPRING}
    >
      {children}
    </motion.span>
  );
}
