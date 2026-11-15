import type { ReactNode } from "react";

/** Shared compact feedback for sidebar loading, empty and error states. */
export function SidebarNotice({ children }: { children: ReactNode }) {
  return <div className="px-4 py-4 text-[length:var(--right-panel-meta-size,var(--fs-sm))] leading-[var(--right-panel-line-height,1.625)] text-text-secondary">{children}</div>;
}
