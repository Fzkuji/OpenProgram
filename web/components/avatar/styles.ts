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
import * as notionists from "@dicebear/notionists";
import * as lorelei from "@dicebear/lorelei";
import * as bottts from "@dicebear/bottts";
import * as initials from "@dicebear/initials";

import type { AvatarStyle } from "./types";

/** Runtime: ``style -> DiceBear namespace``. ``<Avatar>`` looks the
 *  style up here when ``createAvatar`` needs a Style object. */
export const STYLES = {
  shapes,
  notionists,
  lorelei,
  bottts,
  initials,
} as const;

/** UI metadata for the style picker. Order is the order tiles render
 *  in. ``Shapes`` is first because it's the default for new profiles. */
export const AVATAR_STYLES: { id: AvatarStyle; label: string; hint: string }[] = [
  { id: "shapes",     label: "Shapes",     hint: "Abstract geometric (default)" },
  { id: "notionists", label: "Notionists", hint: "Hand-drawn characters" },
  { id: "lorelei",    label: "Lorelei",    hint: "Soft cartoon faces" },
  { id: "bottts",     label: "Bottts",     hint: "Robot avatars" },
  { id: "initials",   label: "Initials",   hint: "Letter on coloured chip" },
];
