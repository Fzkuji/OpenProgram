"use client";

/**
 * Avatar — single component for every "who is this" glyph in the app.
 *
 * Replaces the historical ``<span style={{ background: color }}>{initial}</span>``
 * pattern that lived in user-menu-footer, the chat assistant / user
 * bubbles, and the settings preview. Three render modes:
 *
 *   * ``dicebear`` (default) — generative SVG from @dicebear/core.
 *     One npm package per style; we pre-import five so the bundle
 *     statically knows what to ship. Seeded by ``seed`` (caller
 *     usually passes the agent name) so the same identity always
 *     gets the same glyph across reloads / pages.
 *
 *   * ``upload`` — render a user-supplied file via ``<img>``. PNG /
 *     JPG / SVG / GIF / WebP / APNG all just work because the browser
 *     does the decoding. Animated GIF / WebP play back natively.
 *
 *   * ``letter`` — the legacy coloured-circle-with-initial. Kept as
 *     an explicit choice for users who prefer minimal, and as the
 *     fallback shape when no name is available yet.
 *
 * The component is theme-agnostic and size-agnostic. Pass ``size`` in
 * pixels and you get back a perfectly circular avatar at that size,
 * regardless of the mode.
 */

import { useMemo } from "react";
import { createAvatar } from "@dicebear/core";
import * as shapes from "@dicebear/shapes";
import * as notionists from "@dicebear/notionists";
import * as lorelei from "@dicebear/lorelei";
import * as bottts from "@dicebear/bottts";
import * as initials from "@dicebear/initials";

// Hand-picked subset of DiceBear's 35+ styles — the ones that fit a
// Claude-style warm neutral UI. ``shapes`` is the default because it
// reads as abstract geometric (similar to claude.ai's own avatars).
const STYLES = {
  shapes,
  notionists,
  lorelei,
  bottts,
  initials,
} as const;

export type AvatarKind = "dicebear" | "upload" | "letter";
export type AvatarStyle = keyof typeof STYLES;

export const AVATAR_STYLES: { id: AvatarStyle; label: string; hint: string }[] = [
  { id: "shapes",     label: "Shapes",     hint: "Abstract geometric (default)" },
  { id: "notionists", label: "Notionists", hint: "Hand-drawn characters" },
  { id: "lorelei",    label: "Lorelei",    hint: "Soft cartoon faces" },
  { id: "bottts",     label: "Bottts",     hint: "Robot avatars" },
  { id: "initials",   label: "Initials",   hint: "Letter on coloured chip" },
];

export interface AvatarConfig {
  /** Render mode. Defaults to ``"dicebear"``. */
  kind?: AvatarKind;
  /** DiceBear style key. Used when ``kind === "dicebear"``. */
  style?: AvatarStyle;
  /** Seed string for DiceBear — same seed = same glyph. Falls back
   *  to ``name`` when omitted. */
  seed?: string;
  /** Image URL / data URI / file path for ``kind === "upload"``. */
  file?: string;
  /** One-or-two-char fallback. Used by ``kind === "letter"``, or
   *  silently when DiceBear fails to render. */
  letter?: string;
  /** Background colour for letter mode (CSS colour). */
  bg?: string;
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
  /** Optional title for hover tooltip. */
  title?: string;
}

/** Best-effort one-char extraction from a display name. */
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

  // Pre-render the DiceBear SVG even when we're in upload/letter mode
  // so toggling modes in settings doesn't lose its memo cache. The
  // SVG string is ~1KB, cheap to keep around.
  const svg = useMemo(() => {
    try {
      // The styles export as namespace objects; the createAvatar typing
      // wants a Style<O> but ``import * as`` gives us a namespace that
      // structurally matches. ``as never`` keeps TS quiet without
      // pulling in @dicebear's deep style generics here.
      return createAvatar(STYLES[style] as never, {
        seed,
        size,
      }).toString();
    } catch {
      return null;
    }
  }, [style, seed, size]);

  const sharedStyle: React.CSSProperties = {
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
    const letter =
      (config?.letter || _initialFor(name)).slice(0, 2);
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
