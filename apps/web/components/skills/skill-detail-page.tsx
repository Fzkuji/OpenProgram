"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Markdown } from "@/lib/format-utils/markdown";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SkillDetail } from "@/lib/abilities/skills-store";
import { useTranslation, type Locale } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";
import styles from "./skills-page.module.css";
import { pushPath } from "@/lib/nav/shallow-nav";
import { confirmAction } from "@/components/ui/confirm-dialog";
import { showToast } from "@/lib/format-utils/toast";

type Tab = "skill" | "files" | "versions";

interface InvokeTraceEntry {
  ts: number;
  skill: string;
  query?: string;
  source: string;
  md_hash: string;
}

function encodePath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

function relTime(ms: number, locale: Locale): string {
  const diff = Date.now() - ms;
  if (diff < 0) return locale === "zh" ? "刚刚" : "just now";
  const sec = Math.floor(diff / 1000);
  if (locale === "zh") {
    if (sec < 60) return `${sec} 秒前`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min} 分钟前`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr} 小时前`;
    const d = Math.floor(hr / 24);
    if (d < 7) return `${d} 天前`;
    if (d < 30) return `${Math.floor(d / 7)} 周前`;
    if (d < 365) return `${Math.floor(d / 30)} 个月前`;
    return `${Math.floor(d / 365)} 年前`;
  }
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d < 7) return `${d}d ago`;
  if (d < 30) return `${Math.floor(d / 7)}w ago`;
  if (d < 365) return `${Math.floor(d / 30)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
}

export function SkillDetailPage({ name }: { name: string }) {
  const { t, text, locale } = useTranslation();
  const router = useRouter();
  const setComposerInput = useSessionStore((s) => s.setComposerInput);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("skill");
  const [copied, setCopied] = useState(false);
  // Inline editor
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [saving, setSaving] = useState(false);
  // Files viewer
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  // Invoke stats
  const [trace, setTrace] = useState<InvokeTraceEntry[]>([]);

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError(null);
    (async () => {
      try {
        const r = await fetch(`/api/skills/${encodePath(name)}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (!cancelled) setDetail(data);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [name]);

  // Fetch invoke trace for the sidebar stats
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`/api/skills/${encodePath(name)}/invoke-trace`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ limit: 100 }),
        });
        if (!r.ok) return;
        const data: InvokeTraceEntry[] = await r.json();
        if (!cancelled) setTrace(data);
      } catch {
        /* ignore */
      }
    })();
    return () => { cancelled = true; };
  }, [name]);

  // Lazy-fetch a companion file when one is clicked
  useEffect(() => {
    if (!openFile || !detail) {
      setFileContent(null);
      setFileError(null);
      return;
    }
    let cancelled = false;
    setFileContent(null);
    setFileError(null);
    (async () => {
      try {
        const r = await fetch(
          `/api/skills/${encodePath(detail.name)}/file?path=${encodeURIComponent(openFile)}`,
        );
        if (!r.ok) {
          const t = await r.text().catch(() => "");
          throw new Error(`HTTP ${r.status}: ${t.slice(0, 200)}`);
        }
        const data: { content: string } = await r.json();
        if (!cancelled) setFileContent(data.content);
      } catch (e) {
        if (!cancelled) setFileError(String(e));
      }
    })();
    return () => { cancelled = true; };
  }, [openFile, detail]);

  if (error) {
    return (
      <div className="main min-w-0 overflow-hidden">
        <div className={`${styles.view} h-full flex flex-col overflow-visible`}>
          <div className={styles.topbar}>
            <Button asChild variant="ghost"><Link href="/skills">← {t("nav.skills")}</Link></Button>
            <span className={styles.title}>{name}</span>
          </div>
          <div className="p-5 text-fs-xs text-accent-red">{error}</div>
        </div>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="main min-w-0 overflow-hidden">
        <div className={`${styles.view} h-full flex flex-col overflow-visible`}>
          <div className={styles.topbar}>
            <Button asChild variant="ghost"><Link href="/skills">← {t("nav.skills")}</Link></Button>
            <span className={styles.title}>{text("Loading...", "加载中...")}</span>
          </div>
        </div>
      </div>
    );
  }

  const canDelete = ["project", "user", "remote-cache"].includes(detail.source);
  const installCmd = `openprogram skills install ${detail.name}`;
  // Best-effort: when the skill name has a namespace prefix it usually means
  // it came from a remote source; the prefix is the slug we'd reinstall from.
  const namespace = detail.name.includes("/") ? detail.name.split("/", 1)[0] : "";

  const doCopy = () => {
    navigator.clipboard.writeText(installCmd).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const doDelete = async () => {
    if (!(await confirmAction({
      title: text(`Delete skill "${detail.name}"?`, `删除技能“${detail.name}”？`),
      confirmLabel: text("Delete", "删除"),
      destructive: true,
    }))) return;
    await fetch(`/api/skills/${encodePath(detail.name)}`, { method: "DELETE" });
    router.push("/skills");
  };

  const canEdit = ["project", "user", "remote-cache"].includes(detail.source);

  const startEdit = () => {
    // Reconstruct full file contents from frontmatter + body. The
    // server returns ``body`` post-frontmatter and ``description`` etc.
    // separately — to keep edits faithful we read the raw file via the
    // file endpoint and let the user see the whole thing.
    setEditText(detail.body); // pre-fill with body; user may extend frontmatter via raw text
    setEditing(true);
    // Lazy: also fetch the raw file so frontmatter survives a save.
    fetch(`/api/skills/${encodePath(detail.name)}/file?path=SKILL.md`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data?.content) setEditText(data.content);
      })
      .catch(() => { /* ignore */ });
  };

  const saveEdit = async () => {
    setSaving(true);
    try {
      const r = await fetch(`/api/skills/${encodePath(detail.name)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editText }),
      });
      if (!r.ok) {
        const t = await r.text().catch(() => "");
        throw new Error(`HTTP ${r.status}: ${t.slice(0, 200)}`);
      }
      // Re-fetch detail so the body/frontmatter on screen reflects the save.
      const fresh = await fetch(`/api/skills/${encodePath(detail.name)}`);
      if (fresh.ok) setDetail(await fresh.json());
      setEditing(false);
    } catch (e) {
      showToast(text(`Save failed: ${e}`, `保存失败：${e}`), { tone: "error" });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="main min-w-0 overflow-hidden">
      <div className={`${styles.view} h-full flex flex-col overflow-visible`}>
        <div className={styles.topbar}>
          <Button asChild variant="ghost"><Link href="/skills">← {t("nav.skills")}</Link></Button>
          <span className={styles.title}>{detail.name}</span>
          {detail.version && (
            <span className="rounded-xs border border-border bg-bg-tertiary px-2 py-[1px] text-fs-2xs uppercase tracking-wide text-text-secondary">
              v{detail.version}
            </span>
          )}
          <span className="rounded-xs border border-border bg-bg-tertiary px-2 py-[1px] text-fs-2xs uppercase tracking-wide text-text-secondary">
            {detail.source}
          </span>
          <div className="flex items-center gap-[10px] ml-[auto] pb-[2px]">
            {!editing && (
              <Button size="xs" onClick={() => {
                // Preload the composer with the slash command — the
                // backend expands `/skill X ...` into the SKILL.md
                // preamble at send time. Land on the current chat
                // session if one is open, otherwise on /chat.
                setComposerInput(`/skill ${detail.name} `);
                pushPath(currentSessionId ? `/s/${currentSessionId}` : "/chat");
              }}>{text("Use in chat", "在对话中使用")} →</Button>
            )}
            {canEdit && !editing && (
              <Button size="xs" onClick={startEdit}>{text("Edit", "编辑")}</Button>
            )}
            {editing && (
              <>
                <Button variant="elevated" size="xs" onClick={() => setEditing(false)} disabled={saving}>{t("sidebar.cancel")}</Button>
                <Button size="xs" onClick={saveEdit} disabled={saving}>{saving ? text("Saving...", "保存中...") : text("Save", "保存")}</Button>
              </>
            )}
            {canDelete && !editing && (
              <Button variant="destructive" size="xs" onClick={doDelete}>{t("sidebar.delete")}</Button>
            )}
          </div>
        </div>

        <div className={`${styles.body} ${styles.bodyDetail}`}>
          {/* main column */}
          <div className="min-w-0 overflow-y-auto p-[16px_24px_24px]">
            {detail.description && (
              <p className="text-fs-xs text-text-secondary mb-4">{detail.description}</p>
            )}

            {/* install command card */}
            <div className="mb-4 rounded-md border border-border">
              <div className="flex items-center justify-between border-b border-border px-3 py-2 bg-bg-secondary">
                <span className="text-fs-2xs font-semibold uppercase tracking-wide text-text-secondary">{text("Install", "安装")}</span>
                <button
                  onClick={doCopy}
                  className="text-fs-2xs text-text-secondary hover:text-nav-color-hover"
                >
                  {copied ? text("Copied", "已复制") : text("Copy", "复制")}
                </button>
              </div>
              <pre className="px-3 py-2 text-fs-2xs font-mono text-text-bright overflow-x-auto">
                {installCmd}
              </pre>
            </div>

            {/* tabs: SKILL.md / Files / Versions */}
            <div className="mb-3 flex gap-1 border-b border-border">
              {(["skill", "files", "versions"] as Tab[]).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={
                    "border-b-2 px-3 py-2 text-fs-xs " +
                    (t === tab
                      ? "border-primary text-nav-color-hover"
                      : "border-transparent text-text-secondary hover:text-nav-color-hover")
                  }
                >
                  {t === "skill"
                    ? "SKILL.md"
                    : t === "files"
                      ? `${text("Files", "文件")} (${detail.resources.length})`
                      : text("Versions", "版本")}
                </button>
              ))}
            </div>

            {tab === "skill" && (
              editing ? (
                <textarea
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  spellCheck={false}
                  className="w-full min-h-[60vh] rounded-md border border-border bg-bg-input p-3 text-fs-2xs font-mono text-text-primary outline-none focus:border-accent-blue"
                />
              ) : (
                <div className="prose prose-invert max-w-none text-fs-xs">
                  <Markdown source={detail.body} escapeRawHtml />
                </div>
              )
            )}
            {tab === "files" && (
              <div className="flex gap-4 min-h-[40vh]">
                <ul className="w-[260px] shrink-0 space-y-1 text-fs-2xs">
                  {["SKILL.md", ...detail.resources].map((file) => (
                    <li key={file}>
                      <FileButton name={file} active={openFile === file} onOpen={setOpenFile} />
                    </li>
                  ))}
                  {detail.resources.length === 0 && (
                    <li className="text-text-secondary px-2 py-1">{text("(no companion files)", "（没有附属文件）")}</li>
                  )}
                </ul>
                <div className="flex-1 min-w-0 rounded-md border border-border bg-bg-input p-3 overflow-auto">
                  {!openFile && (
                    <div className="text-fs-2xs text-text-secondary">{text("Select a file to preview.", "选择一个文件进行预览。")}</div>
                  )}
                  {openFile && fileError && (
                    <div className="text-fs-2xs text-accent-red">{fileError}</div>
                  )}
                  {openFile && fileContent === null && !fileError && (
                    <div className="text-fs-2xs text-text-secondary">{text("Loading...", "加载中...")}</div>
                  )}
                  {openFile && fileContent !== null && (
                    <pre className="whitespace-pre-wrap text-fs-2xs font-mono text-text-primary">{fileContent}</pre>
                  )}
                </div>
              </div>
            )}
            {tab === "versions" && (
              <div className="text-fs-2xs text-text-secondary">
                {detail.version
                  ? text(
                      `Current: v${detail.version}. Version history requires the registry to be a ClawHub source.`,
                      `当前：v${detail.version}。版本历史需要注册表来源为 ClawHub。`,
                    )
                  : text(
                      "Version history is only available for remote registries (ClawHub). This skill ships without a version field.",
                      "版本历史仅适用于远程注册表（ClawHub）。这个技能没有版本字段。",
                    )}
              </div>
            )}
          </div>

          {/* right sidebar with metadata */}
          <aside className="border-l border-border bg-bg-secondary/40 overflow-y-auto p-4 text-fs-xs">
            <SideField label={text("Source", "来源")}>
              <span>{detail.source}</span>
            </SideField>

            {detail.category && (
              <SideField label={text("Category", "分类")}>{detail.category}</SideField>
            )}

            {namespace && (
              <SideField label={text("Namespace", "命名空间")}>
                <span>{namespace}/</span>
              </SideField>
            )}

            {detail.version && (
              <SideField label={text("Current version", "当前版本")}>v{detail.version}</SideField>
            )}

            <SideField label={text("Path", "路径")}>
              <span className="text-fs-2xs break-all">{detail.path}</span>
            </SideField>

            <SideField label={text("Aliases", "别名")}>
              {detail.aliases && detail.aliases.length > 0
                ? detail.aliases.join(", ")
                : <span className="text-text-secondary">{text("none", "无")}</span>}
            </SideField>

            <SideField label={text("Allowed tools", "允许的工具")}>
              {detail.allowed_tools && detail.allowed_tools.length > 0
                ? detail.allowed_tools.join(", ")
                : <span className="text-text-secondary">{text("unrestricted", "不限制")}</span>}
            </SideField>

            <SideField label={text("Resources", "资源")}>
              {detail.resources.length > 0
                ? text(
                    `${detail.resources.length} file${detail.resources.length === 1 ? "" : "s"}`,
                    `${detail.resources.length} 个文件`,
                  )
                : <span className="text-text-secondary">{text("SKILL.md only", "仅 SKILL.md")}</span>}
            </SideField>

            <SideField label={text("Enabled", "已启用")}>
              {detail.enabled ? text("yes", "是") : text("no", "否")}
            </SideField>

            <SideField label={text("Invocations", "调用次数")}>
              {trace.length === 0 ? (
                <span className="text-text-secondary">{text("never invoked", "从未调用")}</span>
              ) : (
                <>
                  <div className="text-fs-xs">
                    {trace.length}
                    {trace.length === 100 ? "+" : ""} {text(trace.length === 1 ? "call" : "calls", "次调用")}
                  </div>
                  <div className="mt-1 text-fs-2xs text-text-secondary">
                    {text("last", "最近")}: {relTime(trace[0].ts * 1000, locale)}
                  </div>
                </>
              )}
            </SideField>
          </aside>
        </div>
      </div>
    </div>
  );
}

function FileButton({ name, active, onOpen }: { name: string; active: boolean; onOpen: (name: string) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(name)}
      className={cn(
        "block w-full truncate rounded-xs px-2 py-1 text-left",
        active ? "bg-bg-hover text-nav-color-hover" : "text-text-secondary hover:bg-bg-hover hover:text-nav-color-hover",
      )}
    >
      {name}
    </button>
  );
}

function SideField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="text-fs-2xs font-semibold uppercase tracking-wide text-text-secondary mb-1">
        {label}
      </div>
      <div className="text-fs-xs text-nav-color">{children}</div>
    </div>
  );
}
