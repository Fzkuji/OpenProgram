/**
 * Public surface of the avatar feature.
 *
 * Import path stays short — ``import { Avatar, AvatarPicker } from
 * "@/components/avatar"`` — and callers don't need to know which
 * internal file each piece lives in.
 *
 * Files in this folder:
 *   * ``Avatar``        — renders an avatar (DiceBear / upload / letter)
 *   * ``AvatarPicker``  — settings-page UI for editing an ``AvatarConfig``
 *   * ``styles``        — DiceBear runtime registry
 *   * ``style-options`` — lightweight picker metadata
 *   * ``upload``        — file → data URL helpers + size cap
 *   * ``types``         — ``AvatarConfig``, ``AvatarKind``, ``AvatarStyle``
 */

export { Avatar, type AvatarProps } from "./Avatar";
export {
  AvatarPicker,
  sourceOf,
  type AvatarPickerProps,
  type AvatarSource,
} from "./AvatarPicker";
export { AVATAR_STYLES } from "./style-options";
export { STYLES } from "./styles";
export { UPLOAD_ACCEPT, UPLOAD_MAX_BYTES, fileToDataUrl } from "./upload";
export type {
  AgentAvatarConfig,
  AvatarConfig,
  AvatarKind,
  AvatarStyle,
} from "./types";
