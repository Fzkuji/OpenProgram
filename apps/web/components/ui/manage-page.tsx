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

import type {
  ForwardRefExoticComponent,
  KeyboardEvent,
  ReactNode,
  RefAttributes,
} from "react";
import { useRef } from "react";

import type {
  AnimatedNavIconHandle,
  AnimatedNavIconProps,
} from "@/components/animated-icons";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { isManageActionIcon } from "./manage-action-icon";
import styles from "./manage-page.module.css";

export { styles as managePageStyles };

export type ManageTabIcon = ForwardRefExoticComponent<
  AnimatedNavIconProps & RefAttributes<AnimatedNavIconHandle>
>;

export interface ManageTab {
  id: string;
  label: string;
  /** Optional trailing count, rendered as `label (n)`. */
  count?: number;
  /** Optional pqoqubbw animated icon; hover is driven by the pill. */
  icon?: ManageTabIcon;
}

function ManageTabButton({
  tab,
  active,
  tabIndex,
  onClick,
  onKeyDown,
  secondary = false,
  id,
  ariaControls,
}: {
  tab: ManageTab;
  active: boolean;
  tabIndex: number;
  onClick: () => void;
  onKeyDown?: (event: KeyboardEvent<HTMLButtonElement>) => void;
  secondary?: boolean;
  id?: string;
  ariaControls?: string;
}) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const Icon = tab.icon;
  const label = tab.count === undefined ? tab.label : `${tab.label} (${tab.count})`;
  return (
    <button
      type="button"
      id={id}
      role="tab"
      aria-selected={active}
      aria-controls={ariaControls}
      tabIndex={tabIndex}
      onClick={onClick}
      onKeyDown={onKeyDown}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
      className={cn(styles.tabBtn, secondary && styles.subnavTab, active && styles.active)}
    >
      {Icon ? (
        <span className={styles.tabIcon} aria-hidden>
          <Icon ref={iconRef} size={16} />
        </span>
      ) : null}
      {secondary ? (
        <>
          <span>{tab.label}</span>
          {tab.count !== undefined && <span className={styles.tabCount}>{tab.count}</span>}
        </>
      ) : label}
    </button>
  );
}

export interface ManageAction {
  label: string;
  onClick: () => void;
  /** 16px leading icon: a pqoqubbw component or a lucide node. */
  icon?: ManageTabIcon | ReactNode;
  /** Icon-only square button; label becomes the tooltip. */
  iconOnly?: boolean;
  /** Primary = the page's main call to action (New / Add / Install). */
  primary?: boolean;
  disabled?: boolean;
}

function ManageActionButton({ a }: { a: ManageAction }) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const Icon = isManageActionIcon(a.icon) ? a.icon as ManageTabIcon : null;
  return (
    <Button
      size={a.iconOnly ? "icon" : "sm"}
      variant={a.primary ? "default" : "outline"}
      className={a.primary ? undefined : "border border-[var(--border)] text-[var(--text-primary)]"}
      onClick={a.onClick}
      disabled={a.disabled}
      title={a.iconOnly ? a.label : undefined}
      aria-label={a.iconOnly ? a.label : undefined}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
    >
      {Icon ? <Icon ref={iconRef} size={16} /> : a.icon as ReactNode}
      {!a.iconOnly && a.label}
    </Button>
  );
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
  tabLabel,
  panelId,
  actions = [],
}: {
  title: string;
  tabs?: ManageTab[];
  activeTab?: string;
  onTabChange?: (id: string) => void;
  toolbar?: ReactNode;
  tabLabel?: string;
  panelId?: string;
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
        <div className={styles.tabs} role="tablist" aria-label={tabLabel}>
          {tabs.map((tb, index) => (
            <ManageTabButton
              key={tb.id}
              tab={tb}
              active={activeTab === tb.id}
              tabIndex={activeTab === tb.id ? 0 : -1}
              onClick={() => onTabChange?.(tb.id)}
              onKeyDown={(event) => moveTab(event, index)}
              id={panelId ? `${panelId}-tab-${tb.id}` : undefined}
              ariaControls={panelId}
            />
          ))}
        </div>
      )}
      {(toolbar || actions.length > 0) && (
        <div className={styles.toolbar}>
          {toolbar}
          {actions.map((a) => (
            <ManageActionButton key={a.label} a={a} />
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
  summary,
  action,
  ariaLabel,
  panelId,
}: {
  tabs: ManageTab[];
  activeTab: string;
  onTabChange: (id: string) => void;
  summary?: ReactNode;
  action?: ManageAction;
  ariaLabel: string;
  panelId: string;
}) {
  function moveTab(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
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
    <div className={styles.subnav}>
      <div className={styles.subnavTabs} role="tablist" aria-label={ariaLabel}>
        {tabs.map((tb, index) => (
          <ManageTabButton
            key={tb.id}
            tab={tb}
            active={activeTab === tb.id}
            tabIndex={activeTab === tb.id ? 0 : -1}
            onClick={() => onTabChange(tb.id)}
            onKeyDown={(event) => moveTab(event, index)}
            secondary
            id={`${panelId}-tab-${tb.id}`}
            ariaControls={panelId}
          />
        ))}
      </div>
      <div className={styles.subnavEnd}>
        {summary !== undefined && (
          <div className={styles.subnavSummary} aria-live="polite">{summary}</div>
        )}
        {action && <ManageActionButton a={action} />}
      </div>
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
  const content = (
    <>
      {icon !== undefined && <span className={styles.rowIcon} aria-hidden>{icon}</span>}
      <div className={styles.rowMain}>
        <div className={styles.rowName}>
          <span className={styles.rowLabel}>{name}</span>
          {meta}
        </div>
        {description && <div className={styles.rowDesc}>{description}</div>}
      </div>
      {count !== undefined && <span className={styles.rowCount}>{count}</span>}
    </>
  );
  return (
    <div
      title={title}
      className={cn(styles.row, !onClick && styles.rowStatic, className)}
    >
      {onClick ? (
        <button type="button" className={styles.rowOpen} onClick={onClick}>{content}</button>
      ) : content}
      {actions && (
        <div className={styles.rowActions}>
          {actions}
        </div>
      )}
    </div>
  );
}

/** Shared card anatomy for Plugin, Skill, and MCP discovery catalogs. */
export function ManageCatalogCard({
  title,
  subtitle,
  description,
  meta,
  status,
  actions,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  status?: ReactNode;
  actions: ReactNode;
}) {
  return (
    <article className={styles.catalogCard}>
      <div className={styles.catalogHeader}>
        <div className={styles.catalogTitles}>
          <div className={styles.catalogTitle}>{title}</div>
          {subtitle && <div className={styles.catalogSubtitle}>{subtitle}</div>}
        </div>
        {status && <div className={styles.catalogStatus}>{status}</div>}
      </div>
      {description && <div className={styles.catalogDescription}>{description}</div>}
      <div className={styles.catalogFooter}>
        <div className={styles.catalogMeta}>{meta}</div>
        <div className={styles.catalogActions}>{actions}</div>
      </div>
    </article>
  );
}
