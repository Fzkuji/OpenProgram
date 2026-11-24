import type { ReactNode } from "react";

/** A sidebar's one line for a list that has been read and is empty, or for
 *  a panel with no conversation to show. Never for waiting or a failed read. */
export function SidebarNotice({ children }: { children: ReactNode }) {
  return <div className="px-4 py-4 text-[length:var(--right-panel-meta-size,var(--fs-sm))] leading-[var(--right-panel-line-height,1.625)] text-text-secondary">{children}</div>;
}
