"use client";

/**
 * Shared chrome for OpenProgram management pages such as /skills, /mcp,
 * /plugins and /scheduler.
 *
 * Before this, each page hand-rolled its own topbar, tab pills, action
 * buttons and list rows; the same 64px bar existed three times with
 * three different button styles. These two components + the sibling
 * CSS module are the ONLY shared pieces — anything page-specific
 * (mcp's server rail, plugins' dialogs, skills' discovery) stays put.
 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import styles from "./manage-page.module.css";

export { styles as managePageStyles };

export interface ManageTab {
  id: string;
  label: string;
  /** Optional trailing count, rendered as `label (n)`. */
  count?: number;
}

export interface ManageAction {
  label: string;
  onClick: () => void;
  /** Primary = the page's main call to action (New / Add / Install). */
  primary?: boolean;
  disabled?: boolean;
}

/**
 * The 64px page header: title, optional tab pills, right-aligned custom
 * toolbar content and actions. Every consumer renders the same element
 * tree so heights, gaps and button styling do not drift.
 */
export function ManagePageHeader({
  title,
  tabs,
  activeTab,
  onTabChange,
  toolbar,
  actions = [],
}: {
  title: string;
  tabs?: ManageTab[];
  activeTab?: string;
  onTabChange?: (id: string) => void;
  toolbar?: ReactNode;
  actions?: ManageAction[];
}) {
  function moveTab(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!tabs || !onTabChange) return;
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else return;
    event.preventDefault();
    onTabChange(tabs[next].id);
    event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("button[role=tab]")[next]?.focus();
  }

  return (
    <div className={styles.topbar}>
      <span className={styles.title}>{title}</span>
      {tabs && tabs.length > 0 && (
        <div className={styles.tabs} role="tablist">
          {tabs.map((tb, index) => (
            <button
              key={tb.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tb.id}
              tabIndex={activeTab === tb.id ? 0 : -1}
              onClick={() => onTabChange?.(tb.id)}
              onKeyDown={(event) => moveTab(event, index)}
              className={cn(styles.tabBtn, activeTab === tb.id && styles.active)}
            >
              {tb.count === undefined ? tb.label : `${tb.label} (${tb.count})`}
            </button>
          ))}
        </div>
      )}
      {(toolbar || actions.length > 0) && (
        <div className={styles.toolbar}>
          {toolbar}
          {actions.map((a) => (
            <Button
              key={a.label}
              size="sm"
              variant={a.primary ? "default" : "outline"}
              onClick={a.onClick}
              disabled={a.disabled}
            >
              {a.label}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Slim inner tab row for a page embedded in a hub (no second 64px title).
 * Same pill styles as ManagePageHeader tabs.
 */
export function ManageSubnav({
  tabs,
  activeTab,
  onTabChange,
}: {
  tabs: ManageTab[];
  activeTab: string;
  onTabChange: (id: string) => void;
}) {
  return (
    <div className={styles.subnav} role="tablist">
      {tabs.map((tb) => (
        <button
          key={tb.id}
          type="button"
          role="tab"
          aria-selected={activeTab === tb.id}
          onClick={() => onTabChange(tb.id)}
          className={cn(styles.tabBtn, activeTab === tb.id && styles.active)}
        >
          {tb.count === undefined ? tb.label : `${tb.label} (${tb.count})`}
        </button>
      ))}
    </div>
  );
}

/**
 * One manageable item. `icon` is a 16px node (animated-icons or
 * lucide), `meta` holds badges shown next to the name, `actions`
 * holds the trailing controls (a Switch, buttons, ...).
 */
export function ManageRow({
  icon,
  name,
  description,
  meta,
  count,
  actions,
  onClick,
  title,
  className,
}: {
  icon?: ReactNode;
  name: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  count?: ReactNode;
  actions?: ReactNode;
  onClick?: () => void;
  title?: string;
  className?: string;
}) {
  return (
    <div
      role={onClick ? "button" : undefined}
      onClick={onClick}
      title={title}
      className={cn(styles.row, !onClick && styles.rowStatic, className)}
    >
      {icon !== undefined && <span className={styles.rowIcon} aria-hidden>{icon}</span>}
      <div className={styles.rowMain}>
        <div className={styles.rowName}>
          <span className={styles.rowLabel}>{name}</span>
          {meta}
        </div>
        {description && <div className={styles.rowDesc}>{description}</div>}
      </div>
      {count !== undefined && <span className={styles.rowCount}>{count}</span>}
      {actions && (
        <div className={styles.rowActions} onClick={(e) => e.stopPropagation()}>
          {actions}
        </div>
      )}
    </div>
  );
}
