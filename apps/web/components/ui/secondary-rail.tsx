"use client";

/**
 * The secondary rail: the list column a page shows between the main
 * sidebar and its content (Settings tabs, History projects, Agents; the
 * Programs catalog holds the Files panel's tree instead of rail rows). One anatomy for all of them, built from the main
 * sidebar's row (`ui-list-item`) and the shared kit (SearchInput, Badge,
 * Separator, HoverTip, menu-styles' GROUP_LABEL):
 *
 *   <RailSplit railKey="history">
 *     <SecondaryRail railKey="history" label="Projects">
 *       <RailSearch … /> <RailGroupLabel>…</RailGroupLabel>
 *       <RailItem icon={…} label="…" description="…" badge="…" active />
 *       <RailSeparator /> <RailItem quiet icon={…} label="Add…" />
 *     </SecondaryRail>
 *     <div>content</div>
 *   </RailSplit>
 *
 * A rail with a `railKey` can collapse to an icon column; the page header
 * shows its <RailToggle>. The open state is remembered per key and starts
 * collapsed (and collapses on shrink) below 760px, so a 288px rail never
 * squeezes the content into a sliver.
 */

import Link from "next/link";
import {
  createContext,
  forwardRef,
  useContext,
  useEffect,
  useLayoutEffect,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";

import { PanelLeftCloseIcon, PanelLeftOpenIcon } from "@/components/animated-icons";
import { GROUP_LABEL } from "@/components/ui/menu/menu-styles";
import { sidebarToggleClass } from "@/components/sidebar/nav-classes";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/search-input";
import { Separator } from "@/components/ui/separator";
import { HoverTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import styles from "./secondary-rail.module.css";

const NARROW = "(max-width: 760px)";
/** Indent per tree level, on top of the row's own 8px padding. */
const DEPTH_INDENT = 16;

// ── open state, shared by a page's toggle and its rail ───────────────

const openByKey = new Map<string, boolean>();
const subscribers = new Map<string, Set<(open: boolean) => void>>();

function storageKey(railKey: string) {
  return `${railKey}NavOpen`;
}

function readOpen(railKey: string): boolean {
  if (typeof window === "undefined") return true;
  try {
    const stored = localStorage.getItem(storageKey(railKey));
    if (stored === null) return !window.matchMedia?.(NARROW).matches;
    return stored !== "0";
  } catch {
    return true;
  }
}

function publish(railKey: string, open: boolean, persist: boolean) {
  openByKey.set(railKey, open);
  if (persist) {
    try { localStorage.setItem(storageKey(railKey), open ? "1" : "0"); } catch { /* the page still updates */ }
  }
  subscribers.get(railKey)?.forEach((subscriber) => subscriber(open));
}

/** Whether the rail named `railKey` is open, and a toggle for it. */
export function useRailOpen(railKey: string | undefined): [boolean, () => void] {
  const [open, setOpen] = useState(() => (railKey ? openByKey.get(railKey) ?? true : true));
  useLayoutEffect(() => {
    if (!railKey) return;
    const set = subscribers.get(railKey) ?? new Set();
    subscribers.set(railKey, set);
    set.add(setOpen);
    if (!openByKey.has(railKey)) openByKey.set(railKey, readOpen(railKey));
    setOpen(openByKey.get(railKey)!);
    return () => { set.delete(setOpen); };
  }, [railKey]);
  useEffect(() => {
    if (!railKey) return;
    const query = window.matchMedia?.(NARROW);
    if (!query) return;
    // Not persisted: widening the window again keeps the user's choice.
    const onChange = (event: MediaQueryListEvent) => { if (event.matches) publish(railKey, false, false); };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [railKey]);
  const toggle = () => { if (railKey) publish(railKey, !(openByKey.get(railKey) ?? true), true); };
  return [railKey ? open : true, toggle];
}

/** The header button that collapses / expands a page's rail. */
export function RailToggle({
  railKey,
  label,
  controls,
}: {
  railKey: string;
  /** What the rail lists, e.g. "projects" → "Collapse projects". */
  label: { collapse: string; expand: string };
  controls?: string;
}) {
  const [open, toggle] = useRailOpen(railKey);
  const name = open ? label.collapse : label.expand;
  return (
    <HoverTip label={name} side="bottom">
      <button
        type="button"
        className={sidebarToggleClass}
        onClick={toggle}
        aria-expanded={open}
        aria-controls={controls}
        aria-label={name}
      >
        {open ? <PanelLeftCloseIcon size={20} /> : <PanelLeftOpenIcon size={20} />}
      </button>
    </HoverTip>
  );
}

// ── layout ───────────────────────────────────────────────────────────

/** The rail + content grid. Pass the same `railKey` as the rail. */
export function RailSplit({
  railKey,
  className,
  children,
}: {
  railKey?: string;
  className?: string;
  children: ReactNode;
}) {
  const [open] = useRailOpen(railKey);
  return (
    <div className={cn(styles.split, className)} data-rail={open ? "open" : "collapsed"}>
      {children}
    </div>
  );
}

const CollapsedContext = createContext(false);

export const SecondaryRail = forwardRef<HTMLElement, {
  railKey?: string;
  /** Accessible name of the list, e.g. "Projects". */
  label: string;
  id?: string;
  as?: "nav" | "aside";
  className?: string;
  children: ReactNode;
  "data-testid"?: string;
  [data: `data-${string}`]: string | undefined;
}>(function SecondaryRail({ railKey, label, id, as = "nav", className, children, ...rest }, ref) {
  const [open] = useRailOpen(railKey);
  const Tag = as;
  return (
    <CollapsedContext.Provider value={!open}>
      <Tag
        ref={ref}
        id={id}
        aria-label={label}
        className={cn(styles.rail, className)}
        data-collapsed={open ? undefined : ""}
        data-collapsible={railKey ? "" : undefined}
        {...rest}
      >
        {children}
      </Tag>
    </CollapsedContext.Provider>
  );
});

// ── parts ────────────────────────────────────────────────────────────

export function RailSearch({ action, ...props }: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  "aria-label"?: string;
  /** A control after the box, e.g. a refresh <RailAction>. */
  action?: ReactNode;
}) {
  return (
    <div className={cn(`${styles.search} flex-none mb-[7px]`, action !== undefined && styles.withAction)}>
      <SearchInput className="min-w-0 flex-1" {...props} />
      {action}
    </div>
  );
}

export function RailGroupLabel({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className={cn(GROUP_LABEL, `${styles.groupLabel} pt-[12px]`, action !== undefined && styles.withAction)}>
      {children}
      {action}
    </div>
  );
}

/** A small icon button in a rail's search or group-label row. */
export function RailAction({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <HoverTip label={label}>
      <Button variant="ghost" size="icon-xs" className="ml-auto text-text-muted" onClick={onClick} aria-label={label}>
        {children}
      </Button>
    </HoverTip>
  );
}

export function RailSeparator() {
  return <Separator className={styles.separator} />;
}

/** Quiet count text for a row's trailing slot. */
export function RailCount({ children }: { children: ReactNode }) {
  return <span className={styles.count}>{children}</span>;
}

/** A small outline tag next to a row's name ("Default", "Hidden"). */
export function RailBadge({ children }: { children: ReactNode }) {
  return <Badge variant="outline" className="h-[18px] rounded-xs px-1.5 text-fs-2xs font-normal text-text-muted">{children}</Badge>;
}

type RailItemProps = {
  icon?: ReactNode;
  label: ReactNode;
  /** Plain-text name for the collapsed tooltip when `label` is a node. */
  name?: string;
  description?: ReactNode;
  /** Shown after the name: <RailBadge>s. */
  badge?: ReactNode;
  /** Right-aligned status: a <RailCount>, a warning glyph. */
  trailing?: ReactNode;
  active?: boolean;
  /** Muted text, for an "Add …" row. */
  quiet?: boolean;
  /** Tree depth; nested rows indent and hide when the rail is collapsed. */
  depth?: number;
  href?: string;
  onClick?: () => void;
  className?: string;
  "aria-label"?: string;
  "aria-expanded"?: boolean;
  "data-testid"?: string;
  [data: `data-${string}`]: string | undefined;
};

export function RailItem({
  icon,
  label,
  name,
  description,
  badge,
  trailing,
  active = false,
  quiet = false,
  depth = 0,
  href,
  onClick,
  className,
  ...rest
}: RailItemProps) {
  const collapsed = useContext(CollapsedContext);
  const classes = cn(
    styles.item,
    active && styles.active,
    quiet && styles.quiet,
    description !== undefined && styles.tall,
    depth > 0 && styles.nested,
    className,
  );
  const style = depth > 0 ? ({ paddingLeft: 8 + depth * DEPTH_INDENT } as CSSProperties) : undefined;
  const body = (
    <>
      {icon !== undefined && <span className={styles.icon} aria-hidden>{icon}</span>}
      <span className={`${styles.text} flex-1 min-w-0 flex flex-col`}>
        <span className={styles.label}>
          <span className={styles.labelText}>{label}</span>
          {badge}
        </span>
        {description !== undefined && <span className={styles.description}>{description}</span>}
      </span>
      {trailing !== undefined && <span className={`${styles.trailing} inline-flex items-center gap-[4px] ml-[auto] shrink-0`}>{trailing}</span>}
    </>
  );
  const accessibleName = rest["aria-label"] ?? (typeof label === "string" ? label : name);
  const row = href ? (
    <Link
      href={href}
      className={classes}
      style={style}
      aria-current={active ? "page" : undefined}
      {...rest}
      aria-label={collapsed ? accessibleName : rest["aria-label"]}
    >
      {body}
    </Link>
  ) : (
    <button
      type="button"
      className={classes}
      style={style}
      onClick={onClick}
      aria-current={active ? "true" : undefined}
      {...rest}
      aria-label={collapsed ? accessibleName : rest["aria-label"]}
    >
      {body}
    </button>
  );
  return collapsed && accessibleName ? <HoverTip label={accessibleName} side="right">{row}</HoverTip> : row;
}
