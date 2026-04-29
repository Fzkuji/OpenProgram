import { useContext } from 'react'

import { TerminalSizeContext, type TerminalSize } from '../components/TerminalSizeContext.js'

/**
 * Reactive terminal dimensions. Re-renders the calling component on
 * every resize. Reads from ink's internal TerminalSizeContext —
 * single source of truth for layout. Application code should NOT
 * subscribe to ``process.stdout.on('resize')`` directly: that races
 * with ink's own resize handling and causes split-second layouts
 * where the React tree has new sizes but ink's renderer has old (or
 * vice versa).
 *
 * Returns ``{columns, rows}``. Throws when called outside of an
 * ``<App>`` / ``<AlternateScreen>`` tree, since the context's
 * provider is wired by both.
 */
export function useTerminalSize(): TerminalSize {
  const size = useContext(TerminalSizeContext)
  if (!size) {
    throw new Error(
      'useTerminalSize() must be called inside an Ink <App>/<AlternateScreen> tree',
    )
  }
  return size
}

export type { TerminalSize } from '../components/TerminalSizeContext.js'
