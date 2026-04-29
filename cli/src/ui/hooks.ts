/**
 * Shared hooks for the OpenProgram TUI Kit.
 *
 *   useTerminalSize()   re-exported from the local runtime — every component
 *                       that cares about resize should use this, NOT
 *                       process.stdout subscriptions (race against ink
 *                       internal layout). Throws when called outside of
 *                       a <Shell>/<App> tree.
 *
 *   useBreakpoint()     discrete responsive class. Default thresholds
 *                       roughly mirror common terminal widths:
 *                         < 60 cols  → 'xs'   (tiny pane / split)
 *                         < 100 cols → 'sm'
 *                         < 140 cols → 'md'
 *                         else       → 'lg'
 *
 *   useResponsive(map)  pick a value by breakpoint. Saves the
 *                       per-render switch inside components.
 */
import { useTerminalSize as inkUseTerminalSize, type TerminalSize } from '../runtime/index';

export type { TerminalSize };

export function useTerminalSize(): TerminalSize {
  return inkUseTerminalSize();
}

export type Breakpoint = 'xs' | 'sm' | 'md' | 'lg';

export interface BreakpointThresholds {
  xs?: number;
  sm?: number;
  md?: number;
}

export function useBreakpoint(
  thresholds: BreakpointThresholds = {},
): Breakpoint {
  const { columns } = useTerminalSize();
  const xs = thresholds.xs ?? 60;
  const sm = thresholds.sm ?? 100;
  const md = thresholds.md ?? 140;
  if (columns < xs) return 'xs';
  if (columns < sm) return 'sm';
  if (columns < md) return 'md';
  return 'lg';
}

export type ResponsiveValue<T> = Partial<Record<Breakpoint, T>> & { default: T };

/**
 * Pick a value by current breakpoint, falling back through the chain
 *   xs → sm → md → lg → default
 * so callers only need to specify the smallest size that differs.
 */
export function useResponsive<T>(values: ResponsiveValue<T>): T {
  const bp = useBreakpoint();
  if (bp === 'xs' && values.xs !== undefined) return values.xs;
  if ((bp === 'xs' || bp === 'sm') && values.sm !== undefined) return values.sm;
  if ((bp === 'xs' || bp === 'sm' || bp === 'md') && values.md !== undefined) return values.md;
  if (values.lg !== undefined) return values.lg;
  return values.default;
}
