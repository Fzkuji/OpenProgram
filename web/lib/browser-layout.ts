export const BROWSER_MEDIUM_MAX_WIDTH = 719;
export const BROWSER_NARROW_MAX_WIDTH = 519;

export function browserResponsiveMenuItems(width: number) {
  const medium = width <= BROWSER_MEDIUM_MAX_WIDTH;
  const narrow = width <= BROWSER_NARROW_MAX_WIDTH;
  return {
    home: medium,
    forward: narrow,
    openExternal: medium,
  };
}
