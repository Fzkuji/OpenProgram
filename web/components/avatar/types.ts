/**
 * Shared types for the avatar feature.
 *
 * Single source of truth for:
 *   * What kinds of avatar exist (``AvatarKind``).
 *   * Which DiceBear styles we ship (``AvatarStyle``).
 *   * The serialised config we persist (``AvatarConfig``) — used by
 *     ``<Avatar>``, ``<AvatarPicker>``, and the agent profile in
 *     localStorage.
 *
 * Keeping these decoupled from React lets the agent-style module (a
 * plain ``lib/`` file) reuse them on ``AgentProfilePrefs`` without
 * pulling in the React component tree.
 */

/** Render mode. ``letter`` and ``upload`` map to their own code paths
 *  in ``<Avatar>``; ``dicebear`` defers to ``style`` for the variant. */
export type AvatarKind = "dicebear" | "upload" | "letter";

/** The DiceBear styles we pre-bundle. Anything beyond this needs an
 *  extra ``@dicebear/<style>`` install + a row in ``STYLES`` /
 *  ``AVATAR_STYLES`` in ``styles.ts``. Keys are camelCase even when
 *  the npm package is hyphenated (open-peeps → ``openPeeps``) so they
 *  can be object keys / config string values without quoting. */
export type AvatarStyle =
  | "shapes"
  | "avataaars"
  | "adventurer"
  | "micah"
  | "openPeeps"
  | "personas"
  | "bigSmile"
  | "funEmoji"
  | "bottts"
  | "thumbs"
  | "pixelArt"
  | "identicon"
  | "rings"
  | "initials";

/**
 * Serialisable avatar config. Saved on ``AgentProfilePrefs.avatar``
 * (i.e. localStorage). All fields are optional so old profiles
 * round-trip cleanly — see ``<Avatar>`` for the per-field defaults.
 */
export interface AvatarConfig {
  /** Render mode. Defaults to ``"dicebear"``. */
  kind?: AvatarKind;
  /** DiceBear style key. Used when ``kind === "dicebear"``. */
  style?: AvatarStyle;
  /** Seed string for DiceBear — same seed = same glyph. Falls back
   *  to the display name when omitted. */
  seed?: string;
  /** Image URL / data URI for ``kind === "upload"``. */
  file?: string;
  /** One-or-two-char fallback. Used by ``kind === "letter"``, or
   *  silently when DiceBear fails to render. */
  letter?: string;
  /** Background colour for letter mode (CSS colour). */
  bg?: string;
}

/** Alias kept for ``lib/agent-style.ts`` callers that referenced
 *  ``AgentAvatarConfig`` before the feature was extracted to this
 *  module. Same shape — the extra name documents that this is the
 *  config persisted under ``AgentProfilePrefs.avatar``. */
export type AgentAvatarConfig = AvatarConfig;
