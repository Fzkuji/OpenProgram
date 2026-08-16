/**
 * DiceBear style registry.
 *
 * Runtime handles for every shipped DiceBear style. Adding a new
 * style requires the package import and runtime entry here, plus its
 * UI entry in ``style-options.ts``:
 *
 *   1. ``npm install @dicebear/<style>``
 *   2. Add ``import * as <name> from "@dicebear/<name>";`` below.
 *   3. Add an entry to ``STYLES`` and ``style-options.ts``.
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
} as const satisfies Record<AvatarStyle, unknown>;
