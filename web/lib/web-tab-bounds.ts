export interface WebTabBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function measureWebTabBounds(
  element: Pick<Element, "getBoundingClientRect">,
): WebTabBounds {
  const rect = element.getBoundingClientRect();
  return {
    x: rect.left,
    y: rect.top,
    width: rect.width,
    height: rect.height,
  };
}
