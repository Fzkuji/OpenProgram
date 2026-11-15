import React, { createContext, useContext, useMemo } from 'react';
import type { Key } from '../runtime/ink/events/input-event.js';
import {
  bindingLabel,
  bindingMatches,
  defaultBindings,
  type ResolvedBindings,
} from './resolve.js';

const KeybindingsContext = createContext<ResolvedBindings>(defaultBindings());

export const KeybindingsProvider: React.FC<{
  bindings: ResolvedBindings;
  children: React.ReactNode;
}> = ({ bindings, children }) => (
  <KeybindingsContext.Provider value={bindings}>{children}</KeybindingsContext.Provider>
);

export interface Keybindings {
  bindings: ResolvedBindings;
  matches: (action: string, input: string, key: Key) => boolean;
  label: (action: string) => string;
}

/** The active bindings; without a provider, the defaults. */
export function useKeybindings(): Keybindings {
  const bindings = useContext(KeybindingsContext);
  return useMemo(() => ({
    bindings,
    matches: (action, input, key) => bindingMatches(bindings, action, input, key),
    label: (action) => bindingLabel(bindings, action),
  }), [bindings]);
}
