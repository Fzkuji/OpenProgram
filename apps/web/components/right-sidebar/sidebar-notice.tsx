import type { ReactNode } from "react";

/** Shared compact feedback for sidebar loading, empty and error states. */
export function SidebarNotice({ children }: { children: ReactNode }) {
  return <div className="px-4 py-4 text-[13px] leading-relaxed text-[var(--text-dim)]">{children}</div>;
}
