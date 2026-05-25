"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Markdown } from "@/lib/markdown";
import { Button } from "@/components/ui/button";
import type { SkillDetail } from "@/lib/skills-store";
import styles from "./skills-page.module.css";

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

function relTime(ms: number): string {
  const diff = Date.now() - ms;
  if (diff < 0) return "just now";
  const sec = Math.floor(diff / 1000);
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
  const router = useRouter();
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
      <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
        <div className={styles.view}>
          <div className={styles.topbar}>
            <Link href="/skills" className={styles.tabBtn}>← Skills</Link>
            <span className={styles.title}>{name}</span>
          </div>
          <div className="p-6 text-sm text-[var(--accent-red,#ef4444)]">{error}</div>
        </div>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
        <div className={styles.view}>
          <div className={styles.topbar}>
            <Link href="/skills" className={styles.tabBtn}>← Skills</Link>
            <span className={styles.title}>Loading…</span>
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
    if (!confirm(`Delete skill "${detail.name}"?`)) return;
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
      alert(`Save failed: ${e}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="main" style={{ minWidth: 0, overflow: "hidden" }}>
      <div className={styles.view}>
        <div className={styles.topbar}>
          <Link href="/skills" className={styles.tabBtn}>← Skills</Link>
          <span className={styles.title}>{detail.name}</span>
          {detail.version && (
            <span className="rounded border border-[var(--border)] bg-[var(--bg-tertiary)] px-2 py-[1px] text-[10px] uppercase tracking-wide text-[var(--text-dim)]">
              v{detail.version}
            </span>
          )}
          <span className="rounded border border-[var(--border)] bg-[var(--bg-tertiary)] px-2 py-[1px] text-[10px] uppercase tracking-wide text-[var(--text-dim)]">
            {detail.source}
          </span>
          <div className={styles.toolbar}>
            {canEdit && !editing && (
              <Button variant="outline" size="sm" onClick={startEdit}>Edit</Button>
            )}
            {editing && (
              <>
                <Button variant="outline" size="sm" onClick={() => setEditing(false)} disabled={saving}>Cancel</Button>
                <Button size="sm" onClick={saveEdit} disabled={saving}>{saving ? "Saving…" : "Save"}</Button>
              </>
            )}
            {canDelete && !editing && (
              <Button variant="destructive" size="sm" onClick={doDelete}>Delete</Button>
            )}
          </div>
        </div>

        <div
          className={styles.body}
          style={{ gridTemplateColumns: "1fr 320px" }}
        >
          {/* main column */}
          <div className={styles.content}>
            {detail.description && (
              <p className="text-sm text-[var(--text-secondary)] mb-4">{detail.description}</p>
            )}

            {/* install command card */}
            <div className="mb-5 rounded-md border border-[var(--border)]">
              <div className="flex items-center justify-between border-b border-[var(--border)] px-3 py-2 bg-[var(--bg-secondary)]">
                <span className="text-xs font-semibold uppercase tracking-wide text-[var(--text-dim)]">Install</span>
                <button
                  onClick={doCopy}
                  className="text-xs text-[var(--text-secondary)] hover:text-nav-color-hover"
                >
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
              <pre className="px-3 py-2 text-xs font-mono text-[var(--text-bright)] overflow-x-auto">
                {installCmd}
              </pre>
            </div>

            {/* tabs: SKILL.md / Files / Versions */}
            <div className="mb-3 flex gap-1 border-b border-[var(--border)]">
              {(["skill", "files", "versions"] as Tab[]).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={
                    "border-b-2 px-3 py-2 text-sm " +
                    (t === tab
                      ? "border-primary text-nav-color-hover"
                      : "border-transparent text-[var(--text-secondary)] hover:text-nav-color-hover")
                  }
                >
                  {t === "skill" ? "SKILL.md" : t === "files" ? `Files (${detail.resources.length})` : "Versions"}
                </button>
              ))}
            </div>

            {tab === "skill" && (
              editing ? (
                <textarea
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  spellCheck={false}
                  className="w-full min-h-[60vh] rounded-md border border-[var(--border)] bg-[var(--bg-input)] p-3 text-xs font-mono text-[var(--text-primary)] outline-none focus:border-[var(--accent-blue)] focus:ring-2 focus:ring-[var(--accent-blue)]/20"
                />
              ) : (
                <div className="prose prose-invert max-w-none text-sm">
                  <Markdown source={detail.body} />
                </div>
              )
            )}
            {tab === "files" && (
              <div className="flex gap-4 min-h-[40vh]">
                <ul className="w-[260px] shrink-0 space-y-1 text-xs font-mono">
                  <li>
                    <button
                      onClick={() => setOpenFile("SKILL.md")}
                      className={
                        "block w-full text-left rounded px-2 py-1 truncate " +
                        (openFile === "SKILL.md"
                          ? "bg-[var(--bg-hover)] text-nav-color-hover"
                          : "text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-nav-color-hover")
                      }
                    >
                      SKILL.md
                    </button>
                  </li>
                  {detail.resources.map((r) => (
                    <li key={r}>
                      <button
                        onClick={() => setOpenFile(r)}
                        className={
                          "block w-full text-left rounded px-2 py-1 truncate " +
                          (openFile === r
                            ? "bg-[var(--bg-hover)] text-nav-color-hover"
                            : "text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-nav-color-hover")
                        }
                      >
                        {r}
                      </button>
                    </li>
                  ))}
                  {detail.resources.length === 0 && (
                    <li className="text-[var(--text-tertiary)] px-2 py-1">(no companion files)</li>
                  )}
                </ul>
                <div className="flex-1 min-w-0 rounded-md border border-[var(--border)] bg-[var(--bg-input)] p-3 overflow-auto">
                  {!openFile && (
                    <div className="text-xs text-[var(--text-tertiary)]">Select a file to preview.</div>
                  )}
                  {openFile && fileError && (
                    <div className="text-xs text-[var(--accent-red,#ef4444)]">{fileError}</div>
                  )}
                  {openFile && fileContent === null && !fileError && (
                    <div className="text-xs text-[var(--text-tertiary)]">Loading…</div>
                  )}
                  {openFile && fileContent !== null && (
                    <pre className="whitespace-pre-wrap text-xs font-mono text-[var(--text-primary)]">{fileContent}</pre>
                  )}
                </div>
              </div>
            )}
            {tab === "versions" && (
              <div className="text-xs text-[var(--text-tertiary)]">
                {detail.version
                  ? `Current: v${detail.version}. Version history requires the registry to be a ClawHub source.`
                  : "Version history is only available for remote registries (ClawHub) — this skill ships without a version field."}
              </div>
            )}
          </div>

          {/* right sidebar with metadata */}
          <aside className="border-l border-[var(--border)] bg-[var(--bg-secondary)]/40 overflow-y-auto p-4 text-sm">
            <SideField label="Source">
              <span className="font-mono">{detail.source}</span>
            </SideField>

            {detail.category && (
              <SideField label="Category">{detail.category}</SideField>
            )}

            {namespace && (
              <SideField label="Namespace">
                <span className="font-mono">{namespace}/</span>
              </SideField>
            )}

            {detail.version && (
              <SideField label="Current version">v{detail.version}</SideField>
            )}

            <SideField label="Path">
              <span className="font-mono text-xs break-all">{detail.path}</span>
            </SideField>

            <SideField label="Aliases">
              {detail.aliases && detail.aliases.length > 0
                ? detail.aliases.join(", ")
                : <span className="text-[var(--text-tertiary)]">none</span>}
            </SideField>

            <SideField label="Allowed tools">
              {detail.allowed_tools && detail.allowed_tools.length > 0
                ? detail.allowed_tools.join(", ")
                : <span className="text-[var(--text-tertiary)]">unrestricted</span>}
            </SideField>

            <SideField label="Resources">
              {detail.resources.length > 0
                ? `${detail.resources.length} file${detail.resources.length === 1 ? "" : "s"}`
                : <span className="text-[var(--text-tertiary)]">SKILL.md only</span>}
            </SideField>

            <SideField label="Enabled">
              {detail.enabled ? "yes" : "no"}
            </SideField>

            <SideField label="Invocations">
              {trace.length === 0 ? (
                <span className="text-[var(--text-tertiary)]">never invoked</span>
              ) : (
                <>
                  <div className="text-sm">
                    {trace.length}
                    {trace.length === 100 ? "+" : ""} call{trace.length === 1 ? "" : "s"}
                  </div>
                  <div className="mt-1 text-xs text-[var(--text-tertiary)]">
                    last: {relTime(trace[0].ts * 1000)}
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

function SideField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-[var(--text-tertiary)] mb-1">
        {label}
      </div>
      <div className="text-sm text-nav-color">{children}</div>
    </div>
  );
}
