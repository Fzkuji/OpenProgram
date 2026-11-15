"use client";

/**
 * Avatar — single component for every "who is this" glyph in the app.
 *
 * Replaces the historical ``<span style={{ background: color }}>{initial}
 * </span>`` pattern that lived in user-menu-footer, the chat bubbles,
 * and the settings preview. Three render modes:
 *
 *   * ``dicebear`` (default) — generative SVG via @dicebear/core.
 *     Style + seed picked from ``config``; defaults are ``shapes`` +
 *     the display name so old profiles upgrade with no settings
 *     change. The SVG is reused by ``(style, seed)`` within a bounded cache.
 *
 *   * ``upload`` — render a user-supplied file via ``<img>``. PNG /
 *     JPG / SVG / GIF / WebP / APNG all just work because the browser
 *     does the decoding. Animated GIF / WebP play back natively.
 *
 *   * ``letter`` — the legacy coloured-circle-with-initial. Kept as
 *     an explicit choice for users who prefer minimal, and as the
 *     fallback shape when callers ask for it directly.
 *
 *   * ``bot`` — a character: a coloured shape with two eyes, the way
 *     Agents are pictured. Its silhouette is the picture, so it is not
 *     clipped to a circle. ``live`` animates it and ``mood`` shows what
 *     it is doing; the other modes ignore both.
 *
 * The component is theme-agnostic and size-agnostic. Pass ``size`` in
 * pixels and you get back a perfectly circular avatar at that size,
 * regardless of the mode.
 *
 * Related modules:
 *   * ``./types``    — shape of ``AvatarConfig`` + the kind / style unions
 *   * ``./styles``   — DiceBear style registry (add a style there)
 *   * ``./AvatarPicker`` — the settings-page UI that mutates ``AvatarConfig``
 */

import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { motion, useAnimation, useReducedMotion } from "framer-motion";
import { createAvatar } from "@dicebear/core";

import { HOVER_POP_FILL, HOVER_SPRING, followHostHover } from "../ui/hover-pop";
import { BotAvatar, type BotFitRule, type BotMood } from "./bot/bot-avatar";
import { isBotColor, isBotShape } from "./bot/catalog";
import { botLookFor } from "./bot/identity";
import { loadedAvatarStyle, loadAvatarStyle } from "./styles";
import type { AvatarConfig, AvatarKind, AvatarStyle } from "./types";

const svgCache = new Map<string, string>();
let svgCacheChars = 0;
const SVG_CACHE_CHARS = 512 * 1024;
const SVG_CACHE_ENTRIES = 128;

/** Keep a rendered avatar, evicting the least recently used ones past
 *  either limit. Module-level so the cache outlives any one component. */
function rememberSvg(key: string, svg: string): void {
  if (key.length + svg.length > SVG_CACHE_CHARS) return;
  while (svgCache.size >= SVG_CACHE_ENTRIES || svgCacheChars + key.length + svg.length > SVG_CACHE_CHARS) {
    const oldest = svgCache.keys().next().value!;
    svgCacheChars -= oldest.length + svgCache.get(oldest)!.length;
    svgCache.delete(oldest);
  }
  svgCache.set(key, svg);
  svgCacheChars += key.length + svg.length;
}

export interface AvatarProps {
  /** Pixel diameter. The component renders a perfect circle at this
   *  size regardless of mode. */
  size?: number;
  /** Display name. Drives the alt text + the DiceBear seed fallback
   *  + the letter fallback. */
  name: string;
  /** Optional explicit config — usually pulled from the user's
   *  profile prefs. */
  config?: AvatarConfig;
  /** Forwarded to the outer element so callers can layer their own
   *  class (e.g. CSS-module ``avatar`` for border / hover). */
  className?: string;
  /** Corner radius in px. Defaults to a full circle (9999). Pass a
   *  smaller value for a rounded-square avatar (e.g. message avatars). */
  radius?: number;
  /** ``bot`` only: animate the character (blink, turn, follow the
   *  pointer). Off, it is a still picture. */
  live?: boolean;
  /** ``bot`` only: what the character is doing. */
  mood?: BotMood;
  /** ``bot`` only: the name is written beside the avatar, so the
   *  character itself is decorative. */
  decorative?: boolean;
  /** "mark" is the composer's fill rule, used by the Agent chip and the
   *  Agent menu: a character is sized by its body (botMarkFit); an
   *  uploaded picture, a letter tile or a DiceBear avatar already fills
   *  its box. Either way the box grows to exactly 18 px on its
   *  control's hover (HOVER_POP_FILL). */
  fit?: BotFitRule;
}

/** Best-effort one-char extraction from a display name. Handles
 *  CJK so a name like "助手" still produces a sensible letter chip
 *  in fallback mode. */
function _initialFor(name: string): string {
  for (const ch of name) {
    if (/[a-zA-Z0-9一-鿿]/.test(ch)) return ch.toUpperCase();
  }
  return "?";
}

/** FNV-1a 32-bit hash → short base36 string. Used to derive a stable,
 *  per-(style, seed) prefix for namespacing SVG element ids. Stable
 *  (not random) so server and client render the same markup — a
 *  random prefix would trip React's hydration check. */
function _shortHash(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return (h >>> 0).toString(36);
}

/**
 * Namespace every ``id="…"`` declaration in an SVG string (and the
 * ``url(#…)`` / ``href="#…"`` references that point at them) with a
 * unique prefix.
 *
 * DiceBear's complex styles (avataaars, fun-emoji, bottts) define
 * internal ``<clipPath>`` / ``<mask>`` / ``<linearGradient>`` elements
 * with short ids and reference them via ``url(#id)``. When several of
 * these SVGs are inlined into the SAME document, those ids collide:
 * the browser resolves ``url(#id)`` to the FIRST element with that id
 * in document order, so every avatar after the first gets clipped /
 * masked by the wrong definition and renders broken. Prefixing the
 * ids per render makes each SVG self-contained.
 *
 * Delimiter-anchored replacements (closing quote / paren) mean a short
 * id like ``a`` won't accidentally rewrite ``ab`` — ``url(#a)`` only
 * matches when ``)`` immediately follows.
 */
function _namespaceSvgIds(svg: string, prefix: string): string {
  const ids = new Set<string>();
  const idRe = /\bid="([^"]+)"/g;
  let m: RegExpExecArray | null;
  while ((m = idRe.exec(svg)) !== null) ids.add(m[1]);
  let out = svg;
  for (const id of Array.from(ids)) {
    const safe = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const next = `${prefix}-${id}`;
    out = out
      .replace(new RegExp(`id="${safe}"`, "g"), `id="${next}"`)
      .replace(new RegExp(`url\\(#${safe}\\)`, "g"), `url(#${next})`)
      .replace(
        new RegExp(`(href|xlink:href)="#${safe}"`, "g"),
        `$1="#${next}"`,
      );
  }
  return out;
}

export function Avatar({
  size = 36,
  name,
  config,
  className,
  radius = 9999,
  live,
  mood,
  decorative,
  fit,
}: AvatarProps) {
  const kind: AvatarKind = config?.kind ?? "dicebear";
  const style: AvatarStyle = config?.style ?? "shapes";
  const seed = config?.seed ?? name ?? "default";

  const [loaded, setLoaded] = useState(() => ({ style, generator: loadedAvatarStyle(style) }));
  const generator = loadedAvatarStyle(style) ?? (loaded.style === style ? loaded.generator : undefined);
  useEffect(() => {
    if (kind !== "dicebear" || loadedAvatarStyle(style)) return;
    let disposed = false;
    void loadAvatarStyle(style).then(generator => {
      if (!disposed) setLoaded({ style, generator });
    }).catch(() => {}); // Keep the letter fallback; a later mount can retry.
    return () => { disposed = true; };
  }, [kind, style]);

  // Most styles use a deterministic pastel background so transparent
  // artwork stays visible on both themes. Thumbs is handled separately
  // below because its circular portrait requires no additional background.
  const svg = useMemo(() => {
    if (kind !== "dicebear" || !generator) return null;
    const key = style + "|" + seed;
    const cached = svgCache.get(key);
    if (cached !== undefined) {
      svgCache.delete(key);
      svgCache.set(key, cached);
      return cached;
    }
    try {
      // Don't pass ``size`` to createAvatar — some styles
      // (notionists, lorelei) render their character at a tiny
      // fraction of the SVG viewport, so a 40-px-fixed SVG ends up
      // with a 4-px head that visually disappears. By omitting
      // ``size`` we get an unsized SVG with the style's native
      // viewBox; the wrapping ``<span>`` then scales it down with
      // ``width: 100%; height: 100%`` so the whole character is
      // visible regardless of container size.
      const raw = createAvatar(generator as never, {
        seed,
        // Render at 200px internally — some character styles
        // (notionists, lorelei) draw their bodies into only a
        // fraction of the viewBox at small sizes, so a 40-px
        // bake leaves a 4-px head that visually disappears. Big
        // canvas + CSS-driven shrink keeps the head readable.
        size: 200,
        // Thumbs already draws a complete circular portrait. Its generated
        // rectangular background remains visible around that inset circle,
        // so suppress that layer instead of cropping or scaling the artwork.
        backgroundColor: style === "thumbs" ? ["transparent"] : [
          "b6e3f4",
          "c0aede",
          "d1d4f9",
          "ffd5dc",
          "ffdfbf",
        ],
      }).toString();
      // Rewrite ONLY the attributes in the root <svg> tag — strip
      // width / height (they're HTML attributes that win against
      // CSS sizing) and inject a style that stretches the SVG to
      // fill its container. The capture-group form scopes the
      // strip to the opening tag only, so width="…" inside child
      // <path>/<rect> nodes survives — the buggy first pass that
      // used a global string replace blanked everything because it
      // ate the first child element's own width attribute.
      const sized = raw.replace(
        /<svg\b([^>]*)>/,
        (_match, attrs: string) => {
          const cleaned = attrs
            .replace(/\s+width="[^"]*"/g, "")
            .replace(/\s+height="[^"]*"/g, "");
          return `<svg${cleaned} style="width:100%;height:100%;display:block" preserveAspectRatio="xMidYMid meet">`;
        },
      );
      // Namespace internal ids so multiple inlined avatars on the
      // same page don't share clipPath / mask / gradient ids (which
      // would make every avatar after the first render broken — the
      // browser resolves url(#id) to the first match in the doc).
      // Prefix is a stable hash of (style, seed) so each tile on the
      // settings page gets a distinct namespace, and SSR / client
      // produce identical markup.
      const result = _namespaceSvgIds(sized, "av" + _shortHash(style + "|" + seed));
      rememberSvg(key, result);
      return result;
    } catch {
      return null;
    }
  }, [kind, style, seed, generator]);
  const sharedStyle: CSSProperties = {
    width: size,
    height: size,
    borderRadius: radius,
    flexShrink: 0,
    overflow: "hidden",
    display: "inline-block",
    verticalAlign: "middle",
  };

  if (kind === "bot") {
    const derived = botLookFor(seed);
    return (
      <BotAvatar
        shape={isBotShape(config?.shape) ? config.shape : derived.shape}
        color={isBotColor(config?.color) ? config.color : derived.color}
        size={size}
        live={live}
        mood={mood}
        label={decorative ? "" : name}
        className={className}
        fit={fit}
      />
    );
  }

  // Outside the composer an avatar tile does not move; in the Agent chip
  // and the Agent menu (the "mark" fit) it pops to 18 px like the
  // composer's other icons.
  const placed = (avatar: ReactNode) => fit === "mark"
    ? <MarkPop size={size}>{avatar}</MarkPop>
    : avatar;

  if (kind === "upload" && config?.file) {
    return placed(
      <img
        src={config.file}
        alt={name}
        className={className}
        style={{ ...sharedStyle, objectFit: "cover" }}
      />,
    );
  }

  if (kind === "letter" || (kind === "dicebear" && !svg)) {
    const letter = (config?.letter || _initialFor(name)).slice(0, 2);
    return placed(
      <span
        className={className}
        style={{
          ...sharedStyle,
          background: config?.bg || "var(--avatar-fallback-bg)",
          color: "var(--ink-on-fill)",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: Math.round(size * 0.42), // glyph: the initial scales with the avatar
          fontWeight: 600,
          letterSpacing: letter.length > 1 ? "-0.02em" : undefined,
        }}
      >
        {letter}
      </span>,
    );
  }

  // dicebear path (default). dangerouslySetInnerHTML is the standard
  // way to embed a server-or-client-generated SVG string in React —
  // the markup comes from @dicebear, which does its own escaping.
  return placed(
    <span
      className={className}
      style={sharedStyle}
      dangerouslySetInnerHTML={{ __html: svg ?? "" }}
    />,
  );
}

/** The composer's hover for an Agent tile that already fills its box (an
 *  uploaded picture, a letter tile, a DiceBear avatar): the box grows to
 *  exactly 18 px on its control's hover (HOVER_POP_FILL), as a character
 *  placed by botMarkFit does. */
function MarkPop({ size, children }: { size: number; children: ReactNode }) {
  const self = useRef<HTMLSpanElement>(null);
  const pop = useAnimation();
  const reduced = useReducedMotion();
  useEffect(() => {
    const element = self.current;
    if (!element || reduced) return;
    return followHostHover(element, () => void pop.start("animate"), () => void pop.start("normal"));
  }, [pop, reduced]);
  return (
    <motion.span
      ref={self}
      style={{ display: "inline-flex", width: size, height: size, flexShrink: 0, verticalAlign: "middle" }}
      variants={HOVER_POP_FILL}
      initial="normal"
      animate={pop}
      transition={HOVER_SPRING}
    >
      {children}
    </motion.span>
  );
}
