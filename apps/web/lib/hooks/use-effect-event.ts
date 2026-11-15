"use client";

import { useCallback, useInsertionEffect, useRef } from "react";

/**
 * React 18 stand-in for React 19.2's `useEffectEvent`: a stable function
 * that always runs the latest `fn`. An effect calls it to read current
 * props and state without re-running when they change, so its dependency
 * list names only what should start it again.
 *
 * Call the result from effects and the listeners they register, never
 * during render. The hooks lint rule recognises the name and keeps it out
 * of dependency lists.
 */
export function useEffectEvent<Args extends unknown[], Result>(
  fn: (...args: Args) => Result,
): (...args: Args) => Result {
  const latest = useRef(fn);
  // Insertion effects run before layout effects, so even a layout effect
  // of this render sees this render's fn.
  useInsertionEffect(() => {
    latest.current = fn;
  });
  return useCallback((...args: Args) => latest.current(...args), []);
}
