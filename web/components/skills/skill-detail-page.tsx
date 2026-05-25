"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Markdown } from "@/lib/markdown";
import { Button } from "@/components/ui/button";
import type { SkillDetail } from "@/lib/skills-store";
import styles from "./skills-page.module.css";

type Tab = "skill" | "files" | "versions";

function encodePath(name: string): string {
  return name.split("/").map(encodeURIComponent).join("/");
}

export function SkillDetailPage({ name }: { name: string }) {
  const router = useRouter();
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("skill");
  const [copied, setCopied] = useState(false);

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
            {canDelete && (
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
              <div className="prose prose-invert max-w-none text-sm">
                <Markdown source={detail.body} />
              </div>
            )}
            {tab === "files" && (
              <ul className="space-y-1 text-xs font-mono text-[var(--text-secondary)]">
                <li className="text-[var(--text-bright)]">SKILL.md</li>
                {detail.resources.map((r) => (
                  <li key={r}>{r}</li>
                ))}
                {detail.resources.length === 0 && (
                  <li className="text-[var(--text-tertiary)] not-italic">(no companion files)</li>
                )}
              </ul>
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
