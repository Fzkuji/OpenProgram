/** Where everything sits on an Activity page: the two halves of the track
 *  and the marks on the shared time axis. Marks are percents of the plot, so
 *  a width change only updates CSS variables. */

export interface ActivityGeometry { V: number; L: number; R: number; track: number; P: number; both: boolean }

export function computeActivityGeometry(cardWidth: number): ActivityGeometry {
  const V = cardWidth - 16;
  const both = V >= 528;
  const L = both ? 256 : V;
  const R = both ? Math.min(416, V - 272) : V;
  return { V, L, R, track: L + 16 + R, P: R - 88, both };
}

export function geometryVariables(geometry: ActivityGeometry): Record<"--V" | "--L" | "--R" | "--track" | "--P" | "--pw", string> {
  return {
    "--V": `${geometry.V}px`, "--L": `${geometry.L}px`, "--R": `${geometry.R}px`,
    "--track": `${geometry.track}px`, "--P": `${geometry.P}px`, "--pw": `${geometry.V + 16}px`,
  };
}

export interface ActivityAxis { seconds: number; divisions: number }

export const AXES: readonly (readonly [number, number])[] = [[60, 3], [180, 3], [300, 5], [600, 5], [1800, 3], [3600, 3]];

function axisCovering(seconds: number): ActivityAxis {
  const [axisSeconds, divisions] = AXES.find(([candidate]) => candidate >= seconds) ?? AXES[AXES.length - 1];
  return { seconds: axisSeconds, divisions };
}

/** The smallest axis that covers the finished runs and what happened in the open ones. */
export function axisFor(finishedDurations: readonly number[], openEventOffsets: readonly number[]): ActivityAxis {
  return axisCovering(Math.max(0, ...finishedDurations, ...openEventOffsets));
}

/** A single program's axis: its own length while it fits in a minute. */
export function programAxis(seconds: number): ActivityAxis {
  if (seconds <= 60) return { seconds: Math.max(3, Math.ceil(seconds / 3) * 3), divisions: 3 };
  return axisCovering(seconds);
}

export function axisLabels(axis: ActivityAxis): string[] {
  return Array.from({ length: axis.divisions + 1 }, (_, index) => {
    const value = index * axis.seconds / axis.divisions;
    const last = index === axis.divisions;
    if (axis.seconds <= 60) return last ? `${value}s` : `${value}`;
    return last ? `${value / 60} min` : `${value / 60}`;
  });
}

export function percentOf(seconds: number, axis: ActivityAxis): number {
  return Number((Math.max(0, Math.min(seconds, axis.seconds)) / axis.seconds * 100).toFixed(2));
}

/** A flat right end means no end is known; a run past the axis fills it and keeps its round end. */
export function barOf(startOffset: number, endOffset: number | null, axis: ActivityAxis): { left: number; width: number; flatEnd: boolean } {
  const left = percentOf(startOffset, axis);
  const room = Number((100 - left).toFixed(2));
  if (endOffset === null) return { left, width: room, flatEnd: true };
  return { left, width: Math.min(room, percentOf(endOffset - startOffset, axis)), flatEnd: false };
}

export function tickOf(offset: number, axis: ActivityAxis): { left: number } {
  return { left: percentOf(offset, axis) };
}
