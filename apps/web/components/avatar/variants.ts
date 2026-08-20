import type { AvatarStyle } from "./types";

export interface AvatarVariant {
  style: AvatarStyle;
  seed: string;
}

/** Fresh seeds for a single DiceBear style. Regenerate never
 *  changes the style — only the candidate seeds. */
export function randomAvatarVariants(
  style: AvatarStyle,
  count: number,
): AvatarVariant[] {
  if (!Number.isInteger(count) || count <= 0) return [];

  return Array.from({ length: count }, (_, index) => ({
    style,
    seed: `${Math.random().toString(36).slice(2, 9) || "avatar"}-${index.toString(36)}`,
  }));
}
