export interface EffortColorOption {
  value: string;
}

export function effortLevelColor(
  options: readonly EffortColorOption[],
  value: string,
): string {
  const nonOff = options.filter((option) => option.value !== "off");
  const index = nonOff.findIndex((option) => option.value === value);
  if (value === "off" || index < 0) return "var(--text-bright)";
  const heat = nonOff.length > 1 ? index / (nonOff.length - 1) : 1;
  return `hsl(${Math.round(48 - 48 * heat)}, ${Math.round(96 + 4 * heat)}%, ${Math.round(56 + 12 * heat)}%)`;
}
