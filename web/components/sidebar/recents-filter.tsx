"use client";

/**
 * RecentsFilter — the sliders-icon dropdown on the "Recents" header.
 *
 * Mirrors Claude.ai's conversation filter menu (minus Project /
 * Environment / Last-activity, which don't apply here). Three groups:
 *   - Status:   Active / Archived / All
 *   - Group by: None / Group
 *   - Sort by:  Recency / Title
 *
 * Reads + writes the per-browser view prefs via ``useRecentsView`` /
 * ``setRecentsView`` (lib/recents-view.ts). Pure view state — it does
 * not touch conversation data.
 */

import { useEffect, useState } from "react";

import { useTranslation } from "@/lib/i18n";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  useRecentsView,
  setRecentsView,
  type RecentsStatus,
  type RecentsGroupBy,
  type RecentsSort,
} from "@/lib/recents-view";

export function RecentsFilter() {
  const { t } = useTranslation();
  const view = useRecentsView();
  const [open, setOpen] = useState(false);

  // Non-default view → mark the trigger so users can tell a filter is
  // active without opening the menu.
  const active =
    view.status !== "active" || view.groupBy !== "none" || view.sort !== "recency";

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={t("sidebar.filter")}
          title={t("sidebar.filter")}
          onClick={(e) => e.stopPropagation()}
          className={
            "flex size-[22px] items-center justify-center rounded-[5px]" +
            " transition-colors hover:bg-[var(--bg-hover)] " +
            (active ? "text-[var(--accent-orange)]" : "text-[var(--text-muted)]")
          }
        >
          {/* sliders / filter glyph */}
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
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={6}
        onClick={(e) => e.stopPropagation()}
        className="w-[200px] border border-[var(--border)] bg-[var(--bg-tertiary)] p-1
          text-[var(--text-primary)] shadow-[var(--shadow-popover)]"
      >
        <Group label={t("sidebar.status")}>
          <Choice<RecentsStatus>
            value={view.status}
            options={[
              ["active", t("sidebar.status_active")],
              ["archived", t("sidebar.status_archived")],
              ["all", t("sidebar.status_all")],
            ]}
            onPick={(status) => setRecentsView({ status })}
          />
        </Group>
        <Divider />
        <Group label={t("sidebar.group_by")}>
          <Choice<RecentsGroupBy>
            value={view.groupBy}
            options={[
              ["none", t("sidebar.group_none")],
              ["group", t("sidebar.group_group")],
            ]}
            onPick={(groupBy) => setRecentsView({ groupBy })}
          />
        </Group>
        <Divider />
        <Group label={t("sidebar.sort_by")}>
          <Choice<RecentsSort>
            value={view.sort}
            options={[
              ["recency", t("sidebar.sort_recency")],
              ["title", t("sidebar.sort_title")],
            ]}
            onPick={(sort) => setRecentsView({ sort })}
          />
        </Group>
      </PopoverContent>
    </Popover>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="py-1">
      <div className="px-2 pb-1 text-[10px] uppercase tracking-wide text-[var(--text-muted)]">
        {label}
      </div>
      {children}
    </div>
  );
}

function Divider() {
  return <div className="mx-1 h-px bg-[var(--border)]" />;
}

function Choice<T extends string>({
  value,
  options,
  onPick,
}: {
  value: T;
  options: [T, string][];
  onPick: (v: T) => void;
}) {
  return (
    <div className="flex flex-col">
      {options.map(([val, label]) => {
        const selected = val === value;
        return (
          <button
            key={val}
            type="button"
            onClick={() => onPick(val)}
            className={
              "flex items-center gap-2 rounded-[5px] px-2 h-7 text-[13px]" +
              " cursor-pointer transition-colors hover:bg-[var(--bg-hover)] " +
              (selected ? "text-[var(--text-bright)]" : "text-[var(--text-secondary)]")
            }
          >
            <span className="flex-1 text-left">{label}</span>
            {selected ? (
              <svg width="13" height="13" viewBox="0 0 16 16" fill="none"
                stroke="var(--accent-orange)" strokeWidth="2" strokeLinecap="round"
                strokeLinejoin="round">
                <path d="M3 8.5l3.5 3.5L13 5" />
              </svg>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
