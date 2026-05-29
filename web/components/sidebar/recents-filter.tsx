"use client";

/**
 * RecentsFilter — the sliders-icon dropdown on the "Recents" header.
 *
 * Replicates Claude.ai/code's filter menu shape: a **cascading** menu
 * where each row shows its current value + a ▸ chevron and opens a
 * flyout sub-menu to the right (Status → Active / Archived / All …),
 * rather than a flat list. Built on Radix `DropdownMenu` (Root /
 * Sub / SubTrigger / SubContent / Item) — the primitive designed for
 * exactly this nested-menu pattern (matches Claude's own
 * `role="menuitem" aria-haspopup="menu"` structure).
 *
 * Rows: Status · Group by · Sort by. (Claude also shows Project /
 * Environment / Last activity — omitted here because OpenProgram has
 * no backing data for them; adding dead rows was explicitly avoided.)
 *
 * Reads + writes per-browser view prefs via `useRecentsView` /
 * `setRecentsView` (lib/recents-view.ts) — pure view state.
 */

import { useState } from "react";
import * as DM from "@radix-ui/react-dropdown-menu";

import { useTranslation } from "@/lib/i18n";
import {
  useRecentsView,
  setRecentsView,
  type RecentsStatus,
  type RecentsGroupBy,
  type RecentsSort,
} from "@/lib/recents-view";

const contentCls =
  "z-50 min-w-[150px] rounded-md border border-[var(--border)] bg-[var(--bg-tertiary)]" +
  " p-1 text-[var(--text-primary)] shadow-[var(--shadow-popover)]" +
  " data-[state=open]:animate-in data-[state=closed]:animate-out" +
  " data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0";

export function RecentsFilter() {
  const { t } = useTranslation();
  const view = useRecentsView();
  const [open, setOpen] = useState(false);

  const active =
    view.status !== "active" || view.groupBy !== "none" || view.sort !== "recency";

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
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none"
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
          className={contentCls}
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

          <DM.Separator className="my-1 h-px bg-[var(--border)]" />

          <Row<RecentsGroupBy>
            label={t("sidebar.group_by")}
            value={view.groupBy}
            options={[
              ["none", t("sidebar.group_none")],
              ["group", t("sidebar.group_group")],
            ]}
            onPick={(groupBy) => setRecentsView({ groupBy })}
          />
          <Row<RecentsSort>
            label={t("sidebar.sort_by")}
            value={view.sort}
            options={[
              ["recency", t("sidebar.sort_recency")],
              ["title", t("sidebar.sort_title")],
            ]}
            onPick={(sort) => setRecentsView({ sort })}
          />
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}

/** One cascading row: ``label  <currentValue> ▸`` that opens a flyout
 *  of options with a checkmark on the selected one. */
function Row<T extends string>({
  label,
  value,
  options,
  onPick,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onPick: (v: T) => void;
}) {
  const current = options.find(([v]) => v === value)?.[1] ?? "";
  return (
    <DM.Sub>
      <DM.SubTrigger
        className="flex items-center gap-3 rounded-[5px] px-2 h-8 text-[13px] outline-none
          cursor-pointer select-none data-[state=open]:bg-[var(--bg-hover)]
          data-[highlighted]:bg-[var(--bg-hover)]"
      >
        <span className="flex-1 text-left">{label}</span>
        <span className="text-[var(--text-muted)]">{current}</span>
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none"
          stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"
          strokeLinejoin="round" className="text-[var(--text-muted)]">
          <path d="M6 4l4 4-4 4" />
        </svg>
      </DM.SubTrigger>
      <DM.Portal>
        <DM.SubContent className={contentCls} sideOffset={2} alignOffset={-4}>
          {options.map(([val, optLabel]) => {
            const selected = val === value;
            return (
              <DM.Item
                key={val}
                onSelect={() => onPick(val)}
                className="flex items-center gap-2 rounded-[5px] px-2 h-8 text-[13px]
                  outline-none cursor-pointer select-none
                  data-[highlighted]:bg-[var(--bg-hover)]"
              >
                <span className="flex-1 text-left">{optLabel}</span>
                {selected ? (
                  <svg width="13" height="13" viewBox="0 0 16 16" fill="none"
                    stroke="var(--accent-orange)" strokeWidth="2" strokeLinecap="round"
                    strokeLinejoin="round">
                    <path d="M3 8.5l3.5 3.5L13 5" />
                  </svg>
                ) : (
                  <span className="w-[13px]" />
                )}
              </DM.Item>
            );
          })}
        </DM.SubContent>
      </DM.Portal>
    </DM.Sub>
  );
}
