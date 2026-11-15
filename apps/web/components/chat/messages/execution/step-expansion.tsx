"use client";

/** Whether the reader opened a step, kept per view by (row id, block id) so a
 *  rebuilt block list or a recycled row keeps it. */
import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { useSessionStore } from "@/lib/session-store";

const Scope = createContext<{ view: string; row: string } | null>(null);

export function StepExpansionScope({ view, row, children }: {
  view?: string;
  row?: string;
  children: ReactNode;
}) {
  const value = useMemo(() => (view && row ? { view, row } : null), [view, row]);
  return <Scope.Provider value={value}>{children}</Scope.Provider>;
}

/** The reader's choice for a step, null until they make one. A step with no
 *  id, or outside a transcript row, keeps the choice in the component. */
export function useChosenOpen(blockId?: string): [boolean | null, (open: boolean) => void] {
  const scope = useContext(Scope);
  const key = scope && blockId ? `${scope.row}\u0000${blockId}` : null;
  const saved = useSessionStore((state) => (scope && key ? state.stepExpansion[scope.view]?.[key] : undefined));
  const save = useSessionStore((state) => state.setStepExpansion);
  const [local, setLocal] = useState<boolean | null>(null);
  if (!scope || !key) return [local, setLocal];
  return [saved ?? null, (open) => save(scope.view, key, open)];
}
