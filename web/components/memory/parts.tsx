"use client";

/**
 * Memory page subcomponents — tab button, tree group rows,
 * editor panel, loading skeleton, empty state, placeholder.
 */
import { parseFrontmatter, renderMarkdown } from "./markdown";
import { formatDate } from "./format";
import { DocIcon, TypeBadge } from "./icons";
import type { EditorState, WikiPage } from "./types";
import styles from "./memory-page.module.css";

export function TabButton({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <button className={`${styles.tabBtn} ${active ? styles.tabBtnActive : ""}`} onClick={onClick}>
      {icon}
      {children}
    </button>
  );
}

export function TreeGroup({ folder, pages, expanded, onToggle, selected, onSelect, forceOpen }: {
  folder: string;
  pages: WikiPage[];
  expanded: Set<string>;
  onToggle: (f: string) => void;
  selected: WikiPage | null;
  forceOpen?: boolean;
  onSelect: (p: WikiPage) => void;
}) {
  const isExpanded = !folder || expanded.has(folder) || forceOpen;
  return (
    <div className={styles.treeGroup}>
      {folder && (
        <button className={styles.folderRow} onClick={() => onToggle(folder)}>
          <svg viewBox="0 0 10 10" fill="currentColor" width="8" height="8" className={`${styles.chevron} ${isExpanded ? styles.chevronOpen : ""}`}>
            <path d="M2 3l3 4 3-4H2z"/>
          </svg>
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" width="12" height="12">
            <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5H13A1.5 1.5 0 0 1 14.5 6v6A1.5 1.5 0 0 1 13 13.5H3.5A1.5 1.5 0 0 1 2 12V4.5z"/>
          </svg>
          <span className={styles.folderName}>{folder}</span>
          <span className={styles.folderCount}>{pages.length}</span>
        </button>
      )}
      {isExpanded && (
        <div className={folder ? styles.folderChildren : ""}>
          {pages.map((page) => (
            <button
              key={page.path}
              className={`${styles.fileRow} ${selected?.path === page.path ? styles.fileRowActive : ""}`}
              onClick={() => onSelect(page)}
            >
              <DocIcon className={styles.fileIcon} />
              <span className={styles.fileName}>{page.title || page.path.split("/").pop()}</span>
              {page.type && <TypeBadge type={page.type} />}
              <span className={styles.fileMeta}>{formatDate(page.mtime)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function EditorPanel({ title, badge, meta, state, onChange, onSave, onViewMode, onDelete, onPreviewClick }: {
  title: string;
  badge?: React.ReactNode;
  meta: string[];
  state: EditorState;
  onChange: (c: string) => void;
  onSave: () => void | Promise<void>;
  onViewMode: (m: "edit" | "preview") => void;
  onDelete?: () => void | Promise<void>;
  onPreviewClick?: (e: React.MouseEvent) => void;
}) {
  const { frontmatter, body } = parseFrontmatter(state.content);
  const lines = state.content.split("\n").length;
  const words = state.content.trim() ? state.content.trim().split(/\s+/).length : 0;
  const previewHtml = state.viewMode === "preview" ? renderMarkdown(body) : "";

  return (
    <div className={styles.editor}>
      <div className={styles.editorHeader}>
        <div className={styles.editorHeaderLeft}>
          <DocIcon className={styles.fileIcon} />
          <span className={styles.editorTitle}>{title}</span>
          {badge}
        </div>
        <div className={styles.editorActions}>
          <div className={styles.modeSwitcher}>
            <button className={`${styles.modeBtn} ${state.viewMode === "edit" ? styles.modeBtnActive : ""}`} onClick={() => onViewMode("edit")}>Edit</button>
            <button className={`${styles.modeBtn} ${state.viewMode === "preview" ? styles.modeBtnActive : ""}`} onClick={() => onViewMode("preview")}>Preview</button>
          </div>
          {state.saveStatus === "saved" && <span className={styles.saveOk}>✓ Saved</span>}
          {state.saveStatus === "error" && <span className={styles.saveErr}>✗ Error</span>}
          <button className={styles.saveBtn} onClick={onSave} disabled={state.saving}>
            {state.saving ? "Saving…" : "Save"}
          </button>
          {onDelete && (
            <button className={styles.dangerBtn} onClick={onDelete} title="Delete page">
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" width="12" height="12" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 4h10M6 4V2.5h4V4M5 4l.5 9.5h5L11 4M6.5 6.5v5M9.5 6.5v5"/>
              </svg>
            </button>
          )}
        </div>
      </div>
      {meta.length > 0 && (
        <div className={styles.editorMeta}>{meta.map((m, i) => <span key={i}>{m}</span>)}</div>
      )}
      {state.viewMode === "edit" ? (
        <textarea
          className={styles.textarea}
          value={state.content}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
          placeholder="Empty…"
        />
      ) : (
        <div className={styles.preview} onClick={onPreviewClick}>
          {Object.keys(frontmatter).length > 0 && (
            <div className={styles.frontmatter}>
              {Object.entries(frontmatter).map(([k, v]) => (
                <div key={k} className={styles.fmRow}>
                  <span className={styles.fmKey}>{k}</span>
                  <span className={styles.fmVal}>{v}</span>
                </div>
              ))}
            </div>
          )}
          {body ? (
            <div className={styles.markdown} dangerouslySetInnerHTML={{ __html: previewHtml }} />
          ) : (
            <div className={styles.previewEmpty}>Nothing to preview</div>
          )}
        </div>
      )}
      <div className={styles.editorFooter}>
        <span>{lines} lines</span>
        <span>{words} words</span>
        <span>{state.content.length} chars</span>
      </div>
    </div>
  );
}

export function LoadingSkeleton() {
  return (
    <div className={styles.treeLoading}>
      {[100, 70, 85, 60].map((w, i) => (
        <div key={i} className={styles.skeleton} style={{ width: `${w}%` }} />
      ))}
    </div>
  );
}

export function EmptyState({ icon, text, sub }: { icon: "doc" | "clock"; text: string; sub: string }) {
  return (
    <div className={styles.emptyState}>
      {icon === "doc" ? (
        <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.5" width="40" height="40" opacity="0.3">
          <rect x="8" y="6" width="32" height="36" rx="4"/>
          <path d="M16 18h16M16 24h16M16 30h10"/>
        </svg>
      ) : (
        <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.5" width="40" height="40" opacity="0.3">
          <circle cx="24" cy="24" r="18"/>
          <path d="M24 14v10l6 4"/>
        </svg>
      )}
      <p>{text}</p>
      <span>{sub}</span>
    </div>
  );
}

export function Placeholder({ icon, text }: { icon: "doc" | "clock"; text: string }) {
  return (
    <div className={styles.placeholder}>
      {icon === "doc" ? (
        <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.2" width="44" height="44" opacity="0.2">
          <rect x="8" y="6" width="32" height="36" rx="4"/>
          <path d="M16 18h16M16 24h16M16 30h10"/>
        </svg>
      ) : (
        <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.2" width="44" height="44" opacity="0.2">
          <circle cx="24" cy="24" r="18"/>
          <path d="M24 14v10l6 4"/>
        </svg>
      )}
      <p>{text}</p>
    </div>
  );
}
