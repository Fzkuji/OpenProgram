"use client";

/**
 * Memory page subcomponents — tab button, tree group rows,
 * editor panel, loading skeleton, empty state, placeholder.
 */
import { useRef } from "react";
import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

import { SolarIcon } from "@/components/solar-icons";
import { RailBadge, RailCount, RailItem } from "@/components/ui/secondary-rail";
import { parseFrontmatter, renderMarkdown } from "./markdown/markdown";
import { markOrigins, type TopicOrigins } from "./markdown/origin-marks";
import { Badge } from "@/components/ui/badge";
import { formatDate } from "./markdown/format";
import { DocIcon } from "./icons";
import { useTranslation } from "@/lib/i18n";
import { type AnimatedNavIconHandle, ClockIcon, FileTextIcon, XIcon } from "@/components/animated-icons";
import { ManageEmptyState } from "@/components/ui/manage-page";
import type { EditorState, TopicPage } from "./data/types";
import styles from "./memory-page.module.css";

/** One Memory section (Topics, Timeline, …) in the rail. */
export function TabButton({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return <RailItem icon={icon} label={children} active={active} onClick={onClick} />;
}

/** The folder glyph is the disclosure state, as in the main sidebar's
    project list: the same Solar folder, in --folder-color. */
export function FolderGlyph({ open }: { open: boolean }) {
  return <SolarIcon name={open ? "folder-open" : "folder"} size={16} motionPreset="none" className="text-[var(--folder-color)]" aria-hidden="true" />;
}

export function DocGlyph() {
  return <SolarIcon name="file-text" size={16} motionPreset="none" className="text-text-muted" aria-hidden="true" />;
}

export function TreeGroup({ folder, pages, expanded, onToggle, selected, onSelect, forceOpen }: {
  folder: string;
  pages: TopicPage[];
  expanded: Set<string>;
  onToggle: (f: string) => void;
  selected: TopicPage | null;
  forceOpen?: boolean;
  onSelect: (p: TopicPage) => void;
}) {
  const { locale } = useTranslation();
  const isExpanded = !folder || expanded.has(folder) || Boolean(forceOpen);
  return (
    <>
      {folder && (
        <RailItem
          icon={<FolderGlyph open={isExpanded} />}
          label={folder}
          trailing={<RailCount>{pages.length}</RailCount>}
          aria-expanded={isExpanded}
          onClick={() => onToggle(folder)}
        />
      )}
      {isExpanded && pages.map((page) => (
        <RailItem
          key={page.path}
          depth={folder ? 1 : 0}
          icon={<DocGlyph />}
          label={page.title || page.path.split("/").pop()}
          // Inside its own type's folder the badge would only repeat the folder name.
          badge={page.type && page.type !== folder ? <RailBadge>{page.type}</RailBadge> : undefined}
          trailing={<RailCount>{formatDate(page.mtime, locale)}</RailCount>}
          active={selected?.path === page.path}
          onClick={() => onSelect(page)}
        />
      ))}
    </>
  );
}

export function EditorPanel({ title, badge, meta, state, onChange, onViewMode, onDelete, onPreviewClick, tools, notices, detail, loading, onKeyDown, readOnly, origins }: {
  title: string;
  badge?: React.ReactNode;
  /** Where the file and its paragraphs came from; marks the preview. */
  origins?: TopicOrigins | null;
  meta: string[];
  state: EditorState;
  onChange: (c: string) => void;
  tools?: React.ReactNode;
  notices?: React.ReactNode;
  detail?: React.ReactNode;
  loading?: boolean;
  readOnly?: boolean;
  onKeyDown?: React.KeyboardEventHandler<HTMLTextAreaElement>;
  onViewMode: (m: EditorState["viewMode"]) => void;
  onDelete?: () => void | Promise<void>;
  onPreviewClick?: (e: React.MouseEvent) => void;
}) {
  const { text } = useTranslation();
  const delIconRef = useRef<AnimatedNavIconHandle>(null);
  const { frontmatter, body } = parseFrontmatter(state.content);
  const lines = state.content.split("\n").length;
  const words = state.content.trim() ? state.content.trim().split(/\s+/).length : 0;
  const previewHtml = state.viewMode === "preview"
    ? markOrigins(renderMarkdown(body), origins, text("Added by you", "你添加的"))
    : "";

  return (
    <div className={styles.editor}>
      <div className={styles.editorHeader}>
        <div className={styles.editorHeaderLeft}>
          <DocIcon className={styles.fileIcon} />
          <div className="min-w-0 flex flex-col gap-[5px]">
            <div className="min-w-0 flex items-center gap-[8px]">
              <span className={styles.editorTitle}>{title}</span>
              {badge}
              {origins?.origin === "outside" && (
                <Badge variant="secondary" data-origin="outside">
                  {text("Written outside OpenProgram · read only to memory", "OpenProgram 之外写的 · 记忆只读不改")}
                </Badge>
              )}
            </div>
            {meta.length > 0 && (
              <div className={styles.editorHeaderMeta}>{meta.map((m, i) => <span key={i}>{m}</span>)}</div>
            )}
          </div>
        </div>
        <div className={styles.editorActions}>
          <ToggleGroup type="single" variant="elevated" spacing={1} className="max-[720px]:flex-[1_1_150px]"
            value={state.viewMode === "history" ? "" : state.viewMode}
            onValueChange={(value: string) => { if (value) onViewMode(value as EditorState["viewMode"]); }}>
            <ToggleGroupItem value="edit" className="max-[720px]:flex-1">{text("Edit", "编辑")}</ToggleGroupItem>
            <ToggleGroupItem value="preview" className="max-[720px]:flex-1">{text("Preview", "预览")}</ToggleGroupItem>
          </ToggleGroup>
          {tools}
          {onDelete && (
            <Button variant="ghost" size="icon-xs" className="text-destructive" onClick={onDelete}
              aria-label={text("Delete topic", "删除主题")}
              onMouseEnter={() => delIconRef.current?.startAnimation?.()}
              onMouseLeave={() => delIconRef.current?.stopAnimation?.()}
            >
              <XIcon ref={delIconRef} size={14} />
            </Button>
          )}
        </div>
      </div>
      {notices}
      {loading ? <LoadingSkeleton /> : detail || (state.viewMode === "edit" ? (
        <textarea
          className={styles.textarea}
          aria-label={text("Memory source", "记忆源文本")}
          value={state.content}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
          readOnly={readOnly}
          onKeyDown={onKeyDown}
          placeholder={text("Empty...", "空内容...")}
        />
      ) : (
        <div className="flex-1 overflow-y-auto min-h-0" onClick={onPreviewClick}>
          {Object.keys(frontmatter).length > 0 && (
            <div className={styles.frontmatter}>
              {Object.entries(frontmatter).map(([k, v]) => (
                <div key={k} className="contents">
                  <span className={styles.fmKey}>{k}</span>
                  <span className={styles.fmVal}>{v}</span>
                </div>
              ))}
            </div>
          )}
          {body ? (
            <div className={styles.markdown} dangerouslySetInnerHTML={{ __html: previewHtml }} />
          ) : (
            <div className={styles.previewEmpty}>{text("Nothing to preview", "没有可预览内容")}</div>
          )}
        </div>
      ))}
      <div className={styles.editorFooter}>
        <span>{localeTextCount(lines, text("line", "行"), text("lines", "行"))}</span>
        <span>{localeTextCount(words, text("word", "词"), text("words", "词"))}</span>
        <span>{localeTextCount(state.content.length, text("char", "字符"), text("chars", "字符"))}</span>
      </div>
    </div>
  );
}

function localeTextCount(count: number, singular: string, plural: string): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function LoadingSkeleton() {
  return (
    <div className="p-[12px_10px] flex flex-col gap-[8px]">
      {[100, 70, 85, 60].map((w, i) => (
        <div key={i} className={styles.skeleton} style={{ width: `${w}%` }} />
      ))}
    </div>
  );
}

// Both empty treatments use the app-wide ManageEmptyState anatomy: compact
// in a rail (EmptyState); inside the right pane's framed card, matching the
// Topics emptyPanel, for Placeholder.
function emptyIcon(icon: "doc" | "clock") {
  return icon === "doc" ? <FileTextIcon size={20} /> : <ClockIcon size={20} />;
}

export function EmptyState({ icon, text, sub }: { icon: "doc" | "clock"; text: string; sub: string }) {
  return <ManageEmptyState compact icon={emptyIcon(icon)} title={text} description={sub} />;
}

export function Placeholder({ icon, text }: { icon: "doc" | "clock"; text: string }) {
  return (
    <div className={styles.placeholder}>
      <ManageEmptyState icon={emptyIcon(icon)} title={text} />
    </div>
  );
}
