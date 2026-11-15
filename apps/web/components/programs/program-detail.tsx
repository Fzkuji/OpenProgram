"use client";

/**
 * The Abilities → Programs detail of an application or a tool: the same
 * icon, title and one-sentence description as the Launchpad and the
 * Applications page, then what the Agent can call, the details, and the
 * call logic folded away at the bottom.
 */

import { useState, type ReactNode } from "react";
import { ChevronRight, Info, Star, Wrench } from "lucide-react";

import { ApplicationIcon } from "@/components/applications/application-icon";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";

import { applicationNoteText, programKindLabel, type ProgramExplorerEntry } from "./programs-catalog";
import styles from "./programs-page.module.css";

/** Rows of a list shown before "Show all N". */
const LIST_PREVIEW = 6;

export type ProgramWindow = {
  /** "Opens in a tab, uses the bound project's files", … */
  label: string;
  permissions: string;
  open: () => void;
};

function NameList({ label, rows, testId }: {
  label: string;
  rows: { name: string; description: string; required?: boolean }[];
  testId: string;
}) {
  const { text } = useTranslation();
  const [all, setAll] = useState(false);
  if (!rows.length) return null;
  const shown = all ? rows : rows.slice(0, LIST_PREVIEW);
  return <section className="mt-[26px]" data-testid={testId}>
    <h3 className={styles.detailHeading}>{label}<span>{rows.length}</span></h3>
    <div className={styles.nameList}>
      {shown.map(row => <div key={row.name} className={styles.nameRow}>
        <code>{row.name}{row.required === false ? <small>{text("optional", "可选")}</small> : null}</code>
        <span>{row.description}</span>
      </div>)}
      {rows.length > LIST_PREVIEW && !all
        ? <button type="button" className={styles.showAll} onClick={() => setAll(true)}>{text(`Show all ${rows.length}`, `显示全部 ${rows.length} 个`)}</button>
        : null}
    </div>
  </section>;
}

export function ProgramDetail({
  entry, path, window: programWindow, invocationName, isFavorite, onFavorite, onUse, onEnabled, busy, status, callLogic, callLogicCount,
}: {
  entry: ProgramExplorerEntry;
  path: string;
  window: ProgramWindow | null;
  invocationName: string;
  isFavorite: boolean;
  onFavorite: () => void;
  onUse: () => void;
  onEnabled: ((enabled: boolean) => void) | null;
  busy: boolean;
  /** Load-state line (failed to load, uncommitted changes). */
  status: ReactNode;
  callLogic: ReactNode;
  callLogicCount: string;
}) {
  const { text } = useTranslation();
  const application = entry.program_kind === "application";
  const title = entry.title || entry.name;
  const meta = [
    entry.name,
    programKindLabel({ ...entry, hasWindow: Boolean(programWindow) }, text),
    entry.version ? `v${entry.version}` : "",
  ].filter(Boolean);
  const entries = entry.agent_entries ?? [];
  const note = applicationNoteText(entry.note, text);
  const details: [string, ReactNode][] = [];
  if (entry.source_path) details.push([text("Location", "位置"), <code key="location" className={styles.location}>{entry.location || entry.source_path}</code>]);
  if (programWindow) details.push([text("Window", "窗口"), programWindow.label]);
  if (programWindow?.permissions) details.push([text("Permissions", "权限"), programWindow.permissions]);

  return <>
    <div className="flex items-center gap-[14px]">
      <span className="grid flex-none place-items-center w-[48px] h-[48px]">
        {application
          ? <ApplicationIcon icon={entry.icon} id={entry.application_id || entry.name} size={48} />
          : <span className={styles.toolTile}><Wrench size={20} aria-hidden /></span>}
      </span>
      <div className={`${styles.detailTitle} min-w-0`}>
        <h2>{title}</h2>
        <p>{meta.map((part, index) => <span key={part}>{index === 0 ? <code>{part}</code> : part}</span>)}</p>
      </div>
    </div>
    {status}
    {entry.description ? <p className={styles.detailDescription}>{entry.description}</p> : null}
    <div className="flex flex-wrap items-center gap-[8px] mt-[16px]">
      {programWindow ? <Button disabled={busy || entry.enabled === false} onClick={programWindow.open}>{text("Open", "打开")}</Button> : null}
      {invocationName ? <Button variant={programWindow ? "elevated" : "default"} onClick={onUse}>{text("Use in chat", "在对话中使用")}</Button> : null}
      {invocationName ? <Button
        // A raised button like its neighbours, but the filled star shows
        // the state; the elevated variant's pressed ring would draw a
        // second, accent-coloured frame around it.
        variant="elevated"
        size="icon"
        className="aria-pressed:ring-0"
        aria-label={isFavorite ? text("Unfavorite", "取消收藏") : text("Favorite", "收藏")}
        aria-pressed={isFavorite}
        onClick={onFavorite}
      >
        <Star className={isFavorite ? styles.favoriteIconActive : styles.favoriteIcon} fill={isFavorite ? "currentColor" : "none"} />
      </Button> : null}
      {onEnabled ? <label className={styles.enabledSwitch}>
        <span aria-hidden>{text("Enabled", "已启用")}</span>
        <Switch checked={entry.enabled !== false} disabled={busy} aria-label={text("Enabled", "已启用")} onCheckedChange={onEnabled} />
      </label> : null}
    </div>
    {application
      ? <NameList label={text("Agent can call", "Agent 可调用")} rows={entries} testId="programs-agent-entries" />
      : <NameList label={text("Parameters", "参数")} rows={entry.parameters ?? []} testId="programs-parameters" />}
    {details.length ? <section className="mt-[26px]">
      <h3 className={styles.detailHeading}>{text("Details", "详情")}</h3>
      <dl className={styles.detailList}>
        {details.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}
      </dl>
    </section> : null}
    {note ? <p className={styles.detailNote} role="note" data-testid="programs-application-note">
      <Info size={14} aria-hidden />
      <span>{entry.note?.kind === "ui_only"
        ? text("The Agent cannot call this application; it only opens as a window.", "Agent 不能调用这个应用；它只作为窗口打开。")
        : [note.title, ...note.lines].join(" ")}</span>
    </p> : null}
    <details className={styles.callLogicDisclosure} data-testid="programs-call-logic" data-path={path}>
      <summary>
        <ChevronRight size={14} aria-hidden className={styles.disclosureChevron} />
        <strong>{text("Call logic", "调用逻辑")}</strong>
        <span>{callLogicCount}</span>
      </summary>
      <div className="pb-[8px]">{callLogic}</div>
    </details>
  </>;
}
