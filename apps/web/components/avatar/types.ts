/**
 * Shared types for the avatar feature.
 *
 * Single source of truth for:
 *   * What kinds of avatar exist (``AvatarKind``).
 *   * Which DiceBear styles we ship (``AvatarStyle``).
 *   * The serialised config we persist (``AvatarConfig``) — used by
 *     ``<Avatar>``, ``<AvatarPicker>``, and the user profile in
 *     localStorage.
 *
 * Keeping these decoupled from React lets ``lib/prefs/user-profile.ts``
 * reuse them without pulling in the React component tree.
 */

/** Render mode. ``letter`` and ``upload`` map to their own code paths
 *  in ``<Avatar>``; ``dicebear`` defers to ``style`` for the variant;
 *  ``bot`` is a character (``shape`` + ``color``, see ``./bot``). */
export type AvatarKind = "dicebear" | "upload" | "letter" | "bot";

/** The DiceBear styles we pre-bundle. Anything beyond this needs an
 *  extra ``@dicebear/<style>`` install + a row in ``STYLES`` /
 *  ``AVATAR_STYLES`` in ``style-options.ts``. Keys are camelCase even when
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
 * Serialisable avatar config. Saved on ``UserProfilePrefs.avatar``
 * (i.e. localStorage) and on an Agent's ``avatar``. All fields are optional so old profiles
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
  /** Character shape id for ``kind === "bot"`` (``BOT_SHAPES``). Falls
   *  back to the shape derived from the seed or the name. */
  shape?: string;
  /** Character colour id for ``kind === "bot"`` (``BOT_COLORS``). Falls
   *  back the same way. */
  color?: string;
}
