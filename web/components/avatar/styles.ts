/**
 * DiceBear style registry.
 *
 * Each entry in ``AVATAR_STYLES`` ships with both UI metadata
 * (``label`` / ``hint`` for the picker) and a runtime handle (the
 * DiceBear style namespace, stashed on ``STYLES``). Adding a new
 * style is a 3-line change in this file plus an ``npm install``:
 *
 *   1. ``npm install @dicebear/<style>``
 *   2. Add ``import * as <name> from "@dicebear/<name>";`` below.
 *   3. Add an entry to ``STYLES`` and ``AVATAR_STYLES``.
 *
 * Keeping the registry in one place means ``<Avatar>``,
 * ``<AvatarPicker>``, and the type union in ``types.ts`` stay in sync
 * automatically.
 */

import * as shapes from "@dicebear/shapes";
import * as avataaars from "@dicebear/avataaars";
import * as adventurer from "@dicebear/adventurer";
import * as micah from "@dicebear/micah";
import * as openPeeps from "@dicebear/open-peeps";
import * as personas from "@dicebear/personas";
import * as bigSmile from "@dicebear/big-smile";
import * as funEmoji from "@dicebear/fun-emoji";
import * as bottts from "@dicebear/bottts";
import * as thumbs from "@dicebear/thumbs";
import * as pixelArt from "@dicebear/pixel-art";
import * as identicon from "@dicebear/identicon";
import * as rings from "@dicebear/rings";
import * as initials from "@dicebear/initials";

import type { AvatarStyle } from "./types";

/** Runtime: ``style -> DiceBear namespace``. ``<Avatar>`` looks the
 *  style up here when ``createAvatar`` needs a Style object.
 *
 *  Earlier bundles shipped notionists / lorelei here too, but those
 *  styles draw their characters into only a fraction of the viewBox
 *  and rendered as visually blank tiles at the 40-px picker size. The
 *  styles kept here all fill their viewBox cleanly so they read
 *  correctly at every size we use. Keys are camelCase even where the
 *  npm package is hyphenated (open-peeps → ``openPeeps``). */
export const STYLES = {
  shapes,
  avataaars,
  adventurer,
  micah,
  openPeeps,
  personas,
  bigSmile,
  funEmoji,
  bottts,
  thumbs,
  pixelArt,
  identicon,
  rings,
  initials,
} as const;

/** UI metadata for the style picker. Order is the order tiles render
 *  in — characters first, then geometric / fun, with the minimal
 *  Initials last. ``Shapes`` is first because it's the default for
 *  new profiles. */
export const AVATAR_STYLES: { id: AvatarStyle; label: string; hint: string }[] = [
  { id: "shapes",     label: "Shapes",     hint: "Abstract geometric (default)" },
  { id: "avataaars",  label: "Avataaars",  hint: "Sketch-style portrait characters" },
  { id: "adventurer", label: "Adventurer", hint: "Illustrated adventurer faces" },
  { id: "micah",      label: "Micah",      hint: "Flat illustrated portraits" },
  { id: "openPeeps",  label: "Open Peeps", hint: "Hand-drawn people" },
  { id: "personas",   label: "Personas",  hint: "Clean vector personas" },
  { id: "bigSmile",   label: "Big Smile",  hint: "Cheerful cartoon faces" },
  { id: "funEmoji",   label: "Fun Emoji",  hint: "Simple emoji-like faces" },
  { id: "bottts",     label: "Bottts",     hint: "Robot avatars" },
  { id: "thumbs",     label: "Thumbs",     hint: "Rounded thumb characters" },
  { id: "pixelArt",   label: "Pixel Art",  hint: "8-bit retro characters" },
  { id: "identicon",  label: "Identicon",  hint: "GitHub-style geometric hash" },
  { id: "rings",      label: "Rings",      hint: "Concentric colour rings" },
  { id: "initials",   label: "Initials",   hint: "Letter on coloured chip" },
];
