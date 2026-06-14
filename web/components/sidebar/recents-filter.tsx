"use client";

/**
 * RecentsFilter — the sliders-icon dropdown on the "Recents" header.
 *
 * Replicates Claude.ai/code's filter menu: a CASCADING menu where each
 * row shows its current value + a ▸ chevron and opens a flyout
 * sub-menu to the right. Built on Radix `DropdownMenu`
 * (Root / Sub / SubTrigger / SubContent / Item) — the same nested-menu
 * primitive Claude uses (`role="menuitem" aria-haspopup="menu"`).
 *
 * Rows mirror Claude exactly:
 *   Status · Project · Environment · Last activity
 *   ──────────────── (separator)
 *   Group by · Sort by
 *
 * Status / Last activity are applied by the sidebar today; Project /
 * Environment are wired to the view-pref store but have a single "All"
 * option until a backend supplies the lists (the UI is complete and
 * ready — the filtering hook just needs real data).
 *
 * View prefs persist per-browser via `useRecentsView` / `setRecentsView`
 * (lib/recents-view.ts).
 */

import { Fragment, useMemo, useRef, useState } from "react";
import * as DM from "@radix-ui/react-dropdown-menu";
import { Check, ChevronRight } from "lucide-react";

import { MENU_PANEL, itemCls } from "@/components/chat/top-bar/menu-styles";
import { useTranslation } from "@/lib/i18n";
import { useWindowGlobals } from "./use-window-globals";
import {
  type AnimatedNavIconHandle,
  GalleryVerticalEndIcon,
} from "@/components/animated-icons";
import {
  useRecentsView,
  setRecentsView,
  DEFAULT_RECENTS_VIEW,
  type RecentsStatus,
  type RecentsGroupBy,
  type RecentsSort,
  type RecentsActivity,
} from "@/lib/prefs/recents-view";

// Panel chrome = the canonical MENU_PANEL (the exact same frame as the
// topbar dropdowns and the Recents ⋮ context menu — one shared style so
// every menu in the app reads identically), plus this menu's open/close
// animation. z-50 keeps it above the sidebar.
const surface =
  MENU_PANEL +
  " z-50 text-[var(--text-primary)]" +
  " data-[state=open]:animate-in data-[state=closed]:animate-out" +
  " data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" +
  " data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95";

// A cascading row (SubTrigger / flyout Item) on the shared `itemCls`
// (32px tall · 13px) so these match the context-menu + topbar rows.
const rowCls =
  itemCls(false) +
  " select-none outline-none data-[state=open]:bg-bg-hover" +
  " data-[state=open]:text-text-bright data-[highlighted]:bg-bg-hover" +
  " data-[highlighted]:text-text-bright";

export function RecentsFilter() {
  const { t } = useTranslation();
  const view = useRecentsView();
  const [open, setOpen] = useState(false);
  const filterIconRef = useRef<AnimatedNavIconHandle>(null);

  // Project options come from the conversations themselves — every conv
  // carries a project name (the home-folder name for ad-hoc chats), so
  // the flyout lists "All projects" + each distinct folder the user has
  // chats under, instead of a lone meaningless "All".
  const { conversations } = useWindowGlobals();
  const projectOptions = useMemo<[string, string][]>(() => {
    const names = new Set<string>();
    for (const c of Object.values(conversations || {})) {
      const p = (c as { project?: string }).project;
      if (p) names.add(p);
    }
    return [
      ["all", t("sidebar.all_projects")],
      ...Array.from(names)
        .sort((a, b) => a.localeCompare(b))
        .map((n) => [n, n] as [string, string]),
    ];
  }, [conversations, t]);

  // Any non-default selection lights the trigger so an active filter
  // is visible without opening the menu.
  const active =
    view.status !== DEFAULT_RECENTS_VIEW.status ||
    view.project !== DEFAULT_RECENTS_VIEW.project ||
    view.environment !== DEFAULT_RECENTS_VIEW.environment ||
    view.lastActivity !== DEFAULT_RECENTS_VIEW.lastActivity ||
    view.groupBy !== DEFAULT_RECENTS_VIEW.groupBy ||
    view.sort !== DEFAULT_RECENTS_VIEW.sort;

  return (
    <DM.Root open={open} onOpenChange={setOpen}>
      <DM.Trigger asChild>
        <button
          type="button"
          aria-label={t("sidebar.filter")}
          title={t("sidebar.filter")}
          onClick={(e) => e.stopPropagation()}
          onMouseEnter={() => filterIconRef.current?.startAnimation?.()}
          onMouseLeave={() => filterIconRef.current?.stopAnimation?.()}
          className={
            "flex size-[24px] items-center justify-center rounded-[6px]" +
            " transition-colors hover:bg-[var(--bg-hover)] outline-none " +
            // Stay lit while the menu is open — driven off the component's
            // own `open` state (reliable base utilities) rather than a
            // data-[state] Tailwind variant, which didn't generate here.
            (open ? "bg-[var(--bg-hover)] " : "") +
            (open || active
              ? "text-[var(--text-secondary)]"
              : "text-[var(--text-muted)]")
          }
        >
          <GalleryVerticalEndIcon ref={filterIconRef} size={18} />
        </button>
      </DM.Trigger>

      <DM.Portal>
        <DM.Content
          align="end"
          sideOffset={6}
          className={surface + " min-w-[176px]"}
          onClick={(e) => e.stopPropagation()}
        >
          <Row<RecentsStatus>
            label={t("sidebar.status")}
            value={view.status}
            options={[
              ["active", t("sidebar.status_active")],
              ["archived", t("sidebar.status_archived")],
              ["all", t("sidebar.status_all")],
            ]}
            onPick={(status) => setRecentsView({ status })}
          />
          <Row<string>
            label={t("sidebar.project")}
            value={view.project}
            options={projectOptions}
            onPick={(project) => setRecentsView({ project })}
          />
          <Row<string>
            label={t("sidebar.environment")}
            value={view.environment}
            options={[["all", t("sidebar.filter_all")]]}
            onPick={(environment) => setRecentsView({ environment })}
          />
          <Row<RecentsActivity>
            label={t("sidebar.last_activity")}
            value={view.lastActivity}
            options={[
              ["all", t("sidebar.activity_all")],
              ["1d", t("sidebar.activity_1d")],
              ["7d", t("sidebar.activity_7d")],
              ["30d", t("sidebar.activity_30d")],
            ]}
            onPick={(lastActivity) => setRecentsView({ lastActivity })}
          />

          <DM.Separator className="-mx-[4px] my-[4px] h-px bg-[var(--border)]" />

          <Row<RecentsGroupBy>
            label={t("sidebar.group_by")}
            value={view.groupBy}
            options={[
              ["none", t("sidebar.group_date")],
              ["project", t("sidebar.group_project")],
              ["state", t("sidebar.group_state")],
              ["flat", t("sidebar.group_none")],
            ]}
            // "None" (flat) sits last, set off by a divider — like Claude.
            separateLast
            onPick={(groupBy) => setRecentsView({ groupBy })}
          />
          <Row<RecentsSort>
            label={t("sidebar.sort_by")}
            value={view.sort}
            options={[
              ["title", t("sidebar.sort_title")],
              ["created", t("sidebar.sort_created")],
              ["recency", t("sidebar.sort_recency")],
            ]}
            onPick={(sort) => setRecentsView({ sort })}
          />
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}

/** One cascading row: ``label   <currentValue> ›`` that opens a flyout
 *  of options with an amber check on the selected one. A single-option
 *  row (Project / Environment until backed) still opens — the flyout
 *  just shows the one choice, keeping the layout consistent and ready
 *  for more options later. */
function Row<T extends string>({
  label,
  value,
  options,
  onPick,
  separateLast,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onPick: (v: T) => void;
  /** Render a divider before the last option (e.g. "None" in Group by). */
  separateLast?: boolean;
}) {
  const current = options.find(([v]) => v === value)?.[1] ?? "";
  return (
    <DM.Sub>
      <DM.SubTrigger className={rowCls}>
        <span className="flex-1 text-left">{label}</span>
        <span className="text-text-muted">{current}</span>
        <ChevronRight size={14} className="shrink-0 text-text-muted" />
      </DM.SubTrigger>
      <DM.Portal>
        <DM.SubContent className={surface + " min-w-[160px]"} sideOffset={4} alignOffset={-5}>
          {options.map(([val, optLabel], i) => {
            const selected = val === value;
            const divider = separateLast && i === options.length - 1;
            return (
              <Fragment key={val}>
                {divider ? (
                  <DM.Separator className="-mx-[4px] my-[4px] h-px bg-[var(--border)]" />
                ) : null}
                <DM.Item onSelect={() => onPick(val)} className={rowCls}>
                  <span className="flex-1 text-left">{optLabel}</span>
                  {selected ? (
                    <Check size={14} className="shrink-0" style={{ color: "var(--accent-orange)" }} />
                  ) : (
                    <span className="w-[14px] shrink-0" />
                  )}
                </DM.Item>
              </Fragment>
            );
          })}
        </DM.SubContent>
      </DM.Portal>
    </DM.Sub>
  );
}
