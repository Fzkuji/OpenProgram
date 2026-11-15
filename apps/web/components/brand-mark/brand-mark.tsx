/**
 * Every brand logo in the interface: provider logos, channel logos, and
 * any logo added later. A one-colour logo is drawn through a mask in
 * `color` (the text colour unless the caller gives a brand colour); a logo
 * with its own colours is the picture itself.
 *
 * Two ways to sit in the `size` square box:
 * - "contain" (the default): the whole view box is fitted into the box,
 *   centred, and the logo does not move on hover. Sidebar rows, the
 *   command palette, the Chats page and Settings draw logos this way.
 * - "fill": the composer and the popups it opens. The logo is scaled by
 *   its ink, read from ink-bounds.generated.ts (./fit), until the ink's
 *   longer side is the box's side, centred; on its control's hover the
 *   16 px box grows to exactly 18 px (HOVER_POP_FILL in
 *   @/components/ui/hover-pop).
 */
"use client";

import { useEffect, useRef, type CSSProperties } from "react";
import { motion, useAnimation, useReducedMotion } from "framer-motion";

import { HOVER_POP_FILL, HOVER_SPRING, followHostHover } from "@/components/ui/hover-pop";
import { cn } from "@/lib/utils";

import { fitInk, wholeViewBox, type InkBounds } from "./fit";
import { INK_BOUNDS } from "./ink-bounds.generated";

export interface BrandLogo {
  /** The logo's public path, e.g. "/images/providers/openai.svg". */
  url: string;
  /** A one-colour mark, drawn through a mask, rather than a picture in its own colours. */
  mono: boolean;
}

/** Where a logo's ink sits; a logo the generator has not measured counts
 *  its whole 24-unit view box as ink. */
export function inkBoundsFor(url: string): InkBounds {
  return INK_BOUNDS[url] ?? wholeViewBox();
}

/** A CSS url() for `url`, quoted so no character in it can end the value. */
export function cssUrl(url: string): string {
  return `url("${url.replace(/["\\\n\r\f]/g, (character) => `\\${character.charCodeAt(0).toString(16)} `)}")`;
}

export interface BrandMarkProps {
  logo: BrandLogo;
  /** The box's side in px. */
  size?: number;
  /** Fill of a one-colour logo; the text colour by default. */
  color?: string;
  className?: string;
  /** Data attributes and the like for the box. */
  attributes?: Record<string, string | undefined>;
  /** How the logo sits in its box; see the file comment. */
  fit?: BrandMarkFit;
  /** "contain" only: load a coloured logo lazily. */
  lazy?: boolean;
}

export type BrandMarkFit = "contain" | "fill";

export function BrandMark({ fit = "contain", ...props }: BrandMarkProps) {
  return fit === "fill" ? <FilledBrandMark {...props} /> : <ContainedBrandMark {...props} />;
}

/** The whole logo fitted into the box, centred; no hover motion. */
function ContainedBrandMark({ logo, size = 16, color = "currentColor", className, attributes, lazy }: BrandMarkProps) {
  if (!logo.mono) {
    return (
      <img
        {...attributes}
        src={logo.url}
        alt=""
        aria-hidden="true"
        width={size}
        height={size}
        loading={lazy ? "lazy" : undefined}
        className={cn("block shrink-0", className)}
      />
    );
  }
  const mask = `${cssUrl(logo.url)} center / contain no-repeat`;
  return (
    <span
      {...attributes}
      aria-hidden="true"
      className={cn("block shrink-0", className)}
      style={{ width: size, height: size, backgroundColor: color, mask, WebkitMask: mask }}
    />
  );
}

/** The logo's ink filling the box, popping to 18 px on its control's hover. */
function FilledBrandMark({ logo, size = 16, color = "currentColor", className, attributes }: BrandMarkProps) {
  const self = useRef<HTMLSpanElement>(null);
  const pop = useAnimation();
  const reduced = useReducedMotion();

  useEffect(() => {
    const element = self.current;
    if (!element || reduced) return;
    return followHostHover(element, () => void pop.start("animate"), () => void pop.start("normal"));
  }, [pop, reduced]);

  const fit = fitInk(inkBoundsFor(logo.url), size);
  const drawing: CSSProperties = {
    position: "absolute",
    left: fit.left,
    top: fit.top,
    width: fit.width,
    height: fit.height,
    maxWidth: "none",
    pointerEvents: "none",
  };
  const mask = `${cssUrl(logo.url)} 0 0 / 100% 100% no-repeat`;
  return (
    <motion.span
      ref={self}
      {...attributes}
      aria-hidden="true"
      data-brand-mark="fill"
      className={cn("relative inline-block shrink-0 overflow-visible align-middle", className)}
      style={{ width: size, height: size }}
      variants={HOVER_POP_FILL}
      initial="normal"
      animate={pop}
      transition={HOVER_SPRING}
    >
      {logo.mono ? (
        <span style={{ ...drawing, backgroundColor: color, mask, WebkitMask: mask }} />
      ) : (
        <img src={logo.url} alt="" draggable={false} style={drawing} />
      )}
    </motion.span>
  );
}
