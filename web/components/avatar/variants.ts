import type { AvatarStyle } from "./types";

export interface AvatarVariant {
  style: AvatarStyle;
  seed: string;
}

function shuffle<T>(values: T[]): void {
  for (let index = values.length - 1; index > 0; index -= 1) {
    const target = Math.floor(Math.random() * (index + 1));
    [values[index], values[target]] = [values[target]!, values[index]!];
  }
}

/** Build a random batch that includes every shipped style whenever the
 * requested batch is large enough. Additional slots also use the same
 * registry; each seed includes its index so React keys remain unique. */
export function randomAvatarVariants(
  styleIds: readonly AvatarStyle[],
  count: number,
): AvatarVariant[] {
  if (!Number.isInteger(count) || count <= 0 || styleIds.length === 0) return [];

  const coverage = [...styleIds];
  shuffle(coverage);
  const selectedStyles = coverage.slice(0, count);
  while (selectedStyles.length < count) {
    selectedStyles.push(
      styleIds[Math.floor(Math.random() * styleIds.length)]!,
    );
  }
  shuffle(selectedStyles);

  return selectedStyles.map((style, index) => ({
    style,
    seed: `${Math.random().toString(36).slice(2, 9) || "avatar"}-${index.toString(36)}`,
  }));
}
