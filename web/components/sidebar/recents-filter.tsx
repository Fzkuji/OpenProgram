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

import { Fragment, useState } from "react";
import * as DM from "@radix-ui/react-dropdown-menu";

import { useTranslation } from "@/lib/i18n";
import {
  useRecentsView,
  setRecentsView,
  DEFAULT_RECENTS_VIEW,
  type RecentsStatus,
  type RecentsGroupBy,
  type RecentsSort,
  type RecentsActivity,
} from "@/lib/recents-view";

// Shared menu-surface chrome: rounded card, hairline border, popover
// shadow, theme-tertiary fill. Used by both the root menu and every
// flyout so they read as one consistent system.
const surface =
  "z-50 min-w-[168px] overflow-hidden rounded-[10px] border border-[var(--border)]" +
  " bg-[var(--bg-tertiary)] p-1 text-[var(--text-primary)]" +
  " shadow-[var(--shadow-popover)]" +
  " data-[state=open]:animate-in data-[state=closed]:animate-out" +
  " data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" +
  " data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95";

export function RecentsFilter() {
  const { t } = useTranslation();
  const view = useRecentsView();
  const [open, setOpen] = useState(false);

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
          className={
            "flex size-[22px] items-center justify-center rounded-[5px]" +
            " transition-colors hover:bg-[var(--bg-hover)] outline-none " +
            (active ? "text-[var(--accent-orange)]" : "text-[var(--text-muted)]")
          }
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none"
            stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
            <line x1="2" y1="4" x2="14" y2="4" />
            <line x1="2" y1="8" x2="14" y2="8" />
            <line x1="2" y1="12" x2="14" y2="12" />
            <circle cx="6" cy="4" r="1.6" fill="var(--bg-primary)" />
            <circle cx="10" cy="8" r="1.6" fill="var(--bg-primary)" />
            <circle cx="5" cy="12" r="1.6" fill="var(--bg-primary)" />
          </svg>
        </button>
      </DM.Trigger>

      <DM.Portal>
        <DM.Content
          align="end"
          sideOffset={6}
          className={surface}
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
            options={[["all", t("sidebar.filter_all")]]}
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

          <DM.Separator className="my-1 h-px bg-[var(--border)]" />

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
      <DM.SubTrigger
        className="flex items-center gap-3 rounded-[6px] px-2 h-[26px] text-[12px]
          outline-none cursor-pointer select-none transition-colors
          data-[state=open]:bg-[var(--bg-hover)]
          data-[highlighted]:bg-[var(--bg-hover)]"
      >
        <span className="flex-1 text-left font-medium">{label}</span>
        <span className="text-[var(--text-muted)]">{current}</span>
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none"
          stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
          strokeLinejoin="round" className="text-[var(--text-muted)] shrink-0">
          <path d="M6 4l4 4-4 4" />
        </svg>
      </DM.SubTrigger>
      <DM.Portal>
        <DM.SubContent className={surface} sideOffset={4} alignOffset={-5}>
          {options.map(([val, optLabel], i) => {
            const selected = val === value;
            const divider = separateLast && i === options.length - 1;
            return (
              <Fragment key={val}>
                {divider ? (
                  <DM.Separator className="my-1 h-px bg-[var(--border)]" />
                ) : null}
                <DM.Item
                  onSelect={() => onPick(val)}
                  className="flex items-center gap-3 rounded-[6px] px-2 h-[26px] text-[12px]
                    outline-none cursor-pointer select-none transition-colors
                    data-[highlighted]:bg-[var(--bg-hover)]"
                >
                  <span className="flex-1 text-left">{optLabel}</span>
                  {selected ? (
                    <svg width="14" height="14" viewBox="0 0 16 16" fill="none"
                      stroke="var(--accent-orange)" strokeWidth="2" strokeLinecap="round"
                      strokeLinejoin="round" className="shrink-0">
                      <path d="M3 8.5l3.5 3.5L13 5" />
                    </svg>
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
