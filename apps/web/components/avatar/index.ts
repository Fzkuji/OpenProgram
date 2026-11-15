/**
 * Public surface of the avatar feature.
 *
 * Import path stays short — ``import { Avatar, AvatarPicker } from
 * "@/components/avatar"`` — and callers don't need to know which
 * internal file each piece lives in.
 *
 * Files in this folder:
 *   * ``Avatar``        — renders an avatar (DiceBear / upload / letter / bot)
 *   * ``AvatarPicker``  — settings-page UI for editing an ``AvatarConfig``
 *   * ``styles``        — DiceBear runtime registry
 *   * ``style-options`` — lightweight picker metadata
 *   * ``upload``        — file → data URL helpers + size cap
 *   * ``types``         — ``AvatarConfig``, ``AvatarKind``, ``AvatarStyle``
 *   * ``bot/``          — Bot characters: catalog, identity rule, renderer
 */

export { Avatar, type AvatarProps } from "./avatar";
export {
  AvatarPicker,
  sourceOf,
  type AvatarPickerProps,
  type AvatarSource,
} from "./avatar-picker";
export { AVATAR_STYLES } from "./style-options";
export {
  BOT_COLORS,
  BOT_COLOR_IDS,
  BOT_SHAPES,
  isBotColor,
  isBotShape,
  type BotColor,
  type BotShape,
} from "./bot/catalog";
export { botLookFor, type BotLook } from "./bot/identity";
export type { BotFitRule, BotMood } from "./bot/bot-avatar";
export { STYLES } from "./styles";
export { UPLOAD_ACCEPT, UPLOAD_MAX_BYTES, fileToDataUrl } from "./upload";
export type {
  AvatarConfig,
  AvatarKind,
  AvatarStyle,
} from "./types";
