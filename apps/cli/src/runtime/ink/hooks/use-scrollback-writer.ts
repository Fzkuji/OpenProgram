import { useCallback } from 'react'
import instances from '../instances.js'

/**
 * Returns a function that pushes a string into the terminal's native
 * scrollback buffer ABOVE the live Ink frame, then redraws the frame
 * in the new cursor position. See ``Ink.emitToScrollback`` for the
 * machinery.
 *
 * Common uses:
 *
 *   - chat transcript turns the user has finished reading (so they
 *     stop re-rendering on every keystroke)
 *   - banners / welcomes that should land in scrollback once and stay
 *     there forever
 *   - status notes that scroll up naturally as new ones arrive
 *
 * The hook is stable across renders — ``useCallback`` over the
 * stdout-keyed Ink instance lookup. Safe to put in dependency arrays.
 *
 * Falls back to ``process.stdout.write`` if no Ink instance is
 * registered (e.g. component rendered outside an active ``render()``
 * tree, which shouldn't happen in practice but is defensible).
 */
export function useScrollbackWriter(): (text: string) => void {
  return useCallback((text: string) => {
    const ink = instances.get(process.stdout)
    if (ink) {
      ink.emitToScrollback(text)
    } else {
      process.stdout.write(text)
    }
  }, [])
}

export default useScrollbackWriter
