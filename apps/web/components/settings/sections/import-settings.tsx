"use client";

/**
 * Import settings — bring Claude Code and Codex conversations in as
 * sessions. Lists what /api/sessions/import/candidates finds on this
 * machine, one section per agent, and imports through
 * /api/sessions/import (store/session/external_import on the server).
 * Re-importing is safe server-side, so "Import new" just sends every
 * new or updated transcript.
 */
import { jsonFetch } from "@/lib/net/fetch-client";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatRelativeTime } from "@/lib/format-utils/format";
import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";
import { pushPath } from "@/lib/nav/shallow-nav";
import { SettingsPage, SettingsSection } from "../settings-ui";

type Agent = "claude-code" | "codex";
type State = "new" | "updated" | "imported" | "empty";

interface Candidate {
  agent: Agent;
  agent_label: string;
  source_id: string;
  path: string;
  cwd: string | null;
  title: string;
  updated_at: number;
  state: State;
  session_id: string | null;
}

interface ImportResult {
  status: "created" | "appended" | "unchanged" | "empty" | "failed";
}

const AGENTS: { id: Agent; label: string }[] = [
  { id: "claude-code", label: "Claude Code" },
  { id: "codex", label: "Codex" },
];

/** Rows shown per agent before "Show all". */
const PREVIEW_ROWS = 8;

const pending = (c: Candidate) => c.state === "new" || c.state === "updated";

function folderName(cwd: string | null): string {
  if (!cwd) return "";
  const parts = cwd.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

export function ImportSettings() {
  const { t, text, locale } = useTranslation();
  const [rows, setRows] = useState<Candidate[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<Agent>>(new Set());

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/sessions/import/candidates");
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      setRows(data.candidates ?? []);
      setFailed(false);
    } catch {
      setFailed(true);
      setRows((current) => current ?? []);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function runImport(paths: string[]) {
    if (!paths.length) return;
    setBusy((b) => new Set([...b, ...paths]));
    try {
      const { results } = await jsonFetch<{ results: ImportResult[] }>("/api/sessions/import", {
        method: "POST",
        body: JSON.stringify({ paths }),
      });
      const count = (s: ImportResult["status"]) => results.filter((r) => r.status === s).length;
      const done = count("created") + count("appended");
      const errors = count("failed");
      if (errors) {
        showToast(text(
          `Imported ${done}. ${errors} couldn't be read.`,
          `已导入 ${done} 个，${errors} 个无法读取。`,
        ), { tone: "warn" });
      } else {
        showToast(done
          ? text(`Imported ${done} conversation${done === 1 ? "" : "s"}`, `已导入 ${done} 个对话`)
          : text("Already up to date", "已是最新"));
      }
    } catch {
      showToast(text("Couldn't import. Try again.", "导入失败，请重试。"), { tone: "error" });
    } finally {
      setBusy((b) => new Set([...b].filter((p) => !paths.includes(p))));
      await load();
    }
  }

  const describe = (list: Candidate[]) => {
    if (!list.length) return text("No conversations found on this computer.", "这台电脑上没有找到对话。");
    const imported = list.filter((c) => c.state === "imported" || c.state === "updated").length;
    const updated = list.filter((c) => c.state === "updated").length;
    const parts = [text(`${list.length} conversations`, `${list.length} 个对话`)];
    parts.push(imported ? text(`${imported} imported`, `已导入 ${imported}`) : text("none imported", "尚未导入"));
    if (updated) parts.push(text(`${updated} with new turns`, `${updated} 个有新内容`));
    return parts.join(" · ");
  };

  return (
    <SettingsPage
      title={t("settings.tab.import")}
      description={text(
        "Bring conversations from Claude Code and Codex into OpenProgram. Importing again adds only new turns.",
        "把 Claude Code 和 Codex 的对话导入 OpenProgram。再次导入只会补充新增的回合。",
      )}
    >
      {rows === null ? (
        <p className="text-fs-base text-text-muted">{text("Looking for conversations…", "正在查找对话…")}</p>
      ) : failed && !rows.length ? (
        <p className="text-fs-base text-text-muted">
          {text("Couldn't load conversations. ", "无法加载对话。")}
          <Button variant="link" className="h-auto p-0" onClick={() => void load()}>
            {text("Retry", "重试")}
          </Button>
        </p>
      ) : (
        AGENTS.map(({ id, label }) => {
          const list = rows.filter((c) => c.agent === id);
          const todo = list.filter(pending).map((c) => c.path);
          const showAll = expanded.has(id);
          const shown = showAll ? list : list.slice(0, PREVIEW_ROWS);
          const importingAll = todo.length > 0 && todo.every((p) => busy.has(p));
          return (
            <SettingsSection
              key={id}
              title={label}
              description={describe(list)}
              action={todo.length ? (
                <Button variant="elevated" disabled={importingAll} onClick={() => void runImport(todo)}>
                  {importingAll
                    ? text("Importing…", "正在导入…")
                    : text(`Import new (${todo.length})`, `导入新对话（${todo.length}）`)}
                </Button>
              ) : list.length ? (
                <span className="text-fs-base text-text-muted">{text("Up to date", "已是最新")}</span>
              ) : null}
            >
              {list.length ? (
                <>
                  {shown.map((c) => (
                    <ImportRow
                      key={c.path}
                      row={c}
                      busy={busy.has(c.path)}
                      when={formatRelativeTime(c.updated_at, locale)}
                      onImport={() => void runImport([c.path])}
                    />
                  ))}
                  {list.length > PREVIEW_ROWS ? (
                    <div className="py-2">
                      <Button
                        variant="ghost"
                        onClick={() => setExpanded((e) => {
                          const next = new Set(e);
                          if (showAll) next.delete(id); else next.add(id);
                          return next;
                        })}
                      >
                        {showAll ? text("Show fewer", "收起") : text(`Show all ${list.length}`, `显示全部 ${list.length} 个`)}
                      </Button>
                    </div>
                  ) : null}
                </>
              ) : (
                <p className="py-4 text-fs-base text-text-muted">
                  {id === "claude-code"
                    ? text("Claude Code keeps conversations in ~/.claude/projects.", "Claude Code 的对话保存在 ~/.claude/projects。")
                    : text("Codex keeps conversations in ~/.codex/sessions.", "Codex 的对话保存在 ~/.codex/sessions。")}
                </p>
              )}
            </SettingsSection>
          );
        })
      )}
      {rows?.length ? (
        <p className="text-fs-base text-text-muted">
          {text(
            "Each conversation joins the project of the folder it ran in, which is added when it has none; conversations from your home folder go to your default project.",
            "每条对话归入它所在文件夹的项目，文件夹还不是项目时会自动添加；在主目录里的对话放进默认项目。",
          )}
        </p>
      ) : null}
    </SettingsPage>
  );
}

function ImportRow({
  row,
  busy,
  when,
  onImport,
}: {
  row: Candidate;
  busy: boolean;
  when: string;
  onImport: () => void;
}) {
  const { text } = useTranslation();
  const folder = folderName(row.cwd);
  return (
    <div className="flex items-center gap-3 py-3">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className={"truncate text-fs-base " + (row.state === "empty" ? "text-text-muted" : "text-text-primary")}>
          {row.title || text("Untitled", "未命名")}
        </span>
        <span className="truncate text-fs-xs text-text-muted">
          {folder ? `${when} · ${folder}` : when}
        </span>
      </div>
      {row.state === "updated" ? (
        <Badge variant="secondary">{text("New turns", "有新内容")}</Badge>
      ) : null}
      {row.state === "empty" ? (
        <span className="shrink-0 text-fs-xs text-text-muted">{text("No replies", "没有回复")}</span>
      ) : row.state === "imported" && row.session_id ? (
        <Button variant="ghost" onClick={() => pushPath(`/s/${encodeURIComponent(row.session_id!)}`)}>
          {text("Open", "打开")}
        </Button>
      ) : (
        <Button variant="elevated" disabled={busy} onClick={onImport}>
          {busy
            ? text("Importing…", "正在导入…")
            : row.state === "updated" ? text("Update", "更新") : text("Import", "导入")}
        </Button>
      )}
    </div>
  );
}
