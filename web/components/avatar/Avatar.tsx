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
 *     change. The SVG is memoised on ``(style, seed, size)``.
 *
 *   * ``upload`` — render a user-supplied file via ``<img>``. PNG /
 *     JPG / SVG / GIF / WebP / APNG all just work because the browser
 *     does the decoding. Animated GIF / WebP play back natively.
 *
 *   * ``letter`` — the legacy coloured-circle-with-initial. Kept as
 *     an explicit choice for users who prefer minimal, and as the
 *     fallback shape when callers ask for it directly.
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

import { useMemo, type CSSProperties } from "react";
import { createAvatar } from "@dicebear/core";

import { STYLES } from "./styles";
import type { AvatarConfig, AvatarKind, AvatarStyle } from "./types";

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
  /** Optional title for hover tooltip. */
  title?: string;
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

export function Avatar({
  size = 36,
  name,
  config,
  className,
  title,
}: AvatarProps) {
  const kind: AvatarKind = config?.kind ?? "dicebear";
  const style: AvatarStyle = config?.style ?? "shapes";
  const seed = config?.seed ?? name ?? "default";

  // Pre-render the DiceBear SVG even when we're in upload / letter
  // mode so toggling modes in settings doesn't lose its memo cache.
  // The SVG string is ~1 KB, cheap to keep around.
  //
  // ``backgroundColor`` is set for every style — some styles
  // (notionists, lorelei) default to ``transparent`` and render
  // their characters in dark line art, which on a dark page makes
  // the entire avatar invisible. The palette below is DiceBear's
  // own pastel default; the seed picks deterministically so the
  // same seed always gets the same colour. Styles that already
  // fill their circle (shapes, bottts, initials) draw on top of
  // the background colour, so this is a safe no-op for them.
  const svg = useMemo(() => {
    try {
      // Don't pass ``size`` to createAvatar — some styles
      // (notionists, lorelei) render their character at a tiny
      // fraction of the SVG viewport, so a 40-px-fixed SVG ends up
      // with a 4-px head that visually disappears. By omitting
      // ``size`` we get an unsized SVG with the style's native
      // viewBox; the wrapping ``<span>`` then scales it down with
      // ``width: 100%; height: 100%`` so the whole character is
      // visible regardless of container size.
      //
      // ``backgroundColor`` is set for every style — some styles
      // (notionists, lorelei) default to transparent and render
      // their characters in dark line art, which on a dark page
      // makes the entire avatar invisible. The palette is DiceBear's
      // own pastel default; the seed picks deterministically so the
      // same seed always gets the same colour. Styles that already
      // fill their circle (shapes, bottts, initials) draw on top of
      // the background colour, so this is a safe no-op for them.
      const raw = createAvatar(STYLES[style] as never, {
        seed,
        // Render at 200px internally — some character styles
        // (notionists, lorelei) draw their bodies into only a
        // fraction of the viewBox at small sizes, so a 40-px
        // bake leaves a 4-px head that visually disappears. Big
        // canvas + CSS-driven shrink keeps the head readable.
        size: 200,
        backgroundColor: [
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
      return raw.replace(
        /<svg\b([^>]*)>/,
        (_match, attrs: string) => {
          const cleaned = attrs
            .replace(/\s+width="[^"]*"/g, "")
            .replace(/\s+height="[^"]*"/g, "");
          return `<svg${cleaned} style="width:100%;height:100%;display:block" preserveAspectRatio="xMidYMid meet">`;
        },
      );
    } catch {
      return null;
    }
  }, [style, seed]);

  const sharedStyle: CSSProperties = {
    width: size,
    height: size,
    borderRadius: 9999,
    flexShrink: 0,
    overflow: "hidden",
    display: "inline-block",
    verticalAlign: "middle",
  };

  if (kind === "upload" && config?.file) {
    return (
      <img
        src={config.file}
        alt={name}
        title={title}
        className={className}
        style={{ ...sharedStyle, objectFit: "cover" }}
      />
    );
  }

  if (kind === "letter") {
    const letter = (config?.letter || _initialFor(name)).slice(0, 2);
    return (
      <span
        title={title}
        className={className}
        style={{
          ...sharedStyle,
          background: config?.bg || "#888",
          color: "#fff",
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: Math.round(size * 0.42),
          fontWeight: 600,
          letterSpacing: letter.length > 1 ? "-0.02em" : undefined,
        }}
      >
        {letter}
      </span>
    );
  }

  // dicebear path (default). dangerouslySetInnerHTML is the standard
  // way to embed a server-or-client-generated SVG string in React —
  // the markup comes from @dicebear, which does its own escaping.
  return (
    <span
      title={title}
      className={className}
      style={sharedStyle}
      dangerouslySetInnerHTML={{ __html: svg ?? "" }}
    />
  );
}
