"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  FolderOpen,
  Highlighter,
  ListFilter,
  ScanSearch,
  Search,
  X,
} from "lucide-react";

import { useTranslation } from "@/lib/i18n";
import { matchingIndexes } from "./explorer-search";
import styles from "./files-panel.module.css";

export type ExplorerSearchMode = "filter" | "highlight";

export async function copyText(value: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
  } catch {
    // Use the selection fallback when clipboard permission is unavailable.
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

export function ExplorerMatchText({
  value,
  query,
  fuzzy,
  current = false,
  className,
}: {
  value: string;
  query: string;
  fuzzy: boolean;
  current?: boolean;
  className?: string;
}) {
  const indexes = matchingIndexes(value, query, fuzzy);
  if (!indexes?.length) return <span className={className}>{value}</span>;
  const pieces: ReactNode[] = [];
  let cursor = 0;
  let start = indexes[0];
  for (let index = 1; index <= indexes.length; index += 1) {
    if (index < indexes.length && indexes[index] === indexes[index - 1] + 1) continue;
    const end = indexes[index - 1] + 1;
    if (start > cursor) pieces.push(value.slice(cursor, start));
    pieces.push(
      <span
        className={`${styles.treeNameMatch} ${current ? styles.treeNameMatchCurrent : ""}`}
        key={`${start}:${end}`}
      >
        {value.slice(start, end)}
      </span>,
    );
    cursor = end;
    start = indexes[index];
  }
  if (cursor < value.length) pieces.push(value.slice(cursor));
  return <span className={className}>{pieces}</span>;
}

export function ExplorerHeader({
  leading,
  rootName,
  rootPath,
  showRootPath = true,
  actions,
  searchOpen,
  onSearchOpenChange,
  query,
  onQueryChange,
  mode,
  onModeChange,
  fuzzy,
  onFuzzyChange,
  resultCount,
  resultIndex,
  onMoveResult,
  hideSearch = false,
}: {
  leading?: ReactNode;
  rootName: string;
  rootPath: string | null;
  showRootPath?: boolean;
  actions?: ReactNode;
  searchOpen: boolean;
  onSearchOpenChange: (open: boolean) => void;
  query: string;
  onQueryChange: (query: string) => void;
  mode: ExplorerSearchMode;
  onModeChange: (mode: ExplorerSearchMode) => void;
  fuzzy: boolean;
  onFuzzyChange: (fuzzy: boolean) => void;
  resultCount: number;
  resultIndex: number;
  onMoveResult: (delta: number) => void;
  hideSearch?: boolean;
}) {
  const { text } = useTranslation();
  const [pathExpanded, setPathExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const revealTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  const clearRevealTimer = () => {
    if (revealTimer.current) clearTimeout(revealTimer.current);
    revealTimer.current = null;
  };

  useEffect(() => () => clearRevealTimer(), []);
  useEffect(() => {
    if (searchOpen) searchRef.current?.focus();
  }, [searchOpen]);

  const closeSearch = () => {
    onQueryChange("");
    onSearchOpenChange(false);
  };

  return (
    <div className={styles.treeHeader}>
      <div
        className={`${styles.treeToolbar} ${showRootPath && pathExpanded ? styles.treeToolbarExpanded : ""}`}
        onMouseLeave={() => {
          clearRevealTimer();
          setPathExpanded(false);
        }}
      >
        {leading}
        {showRootPath ? (
          <div className={styles.treeRootPath}>
            <FolderOpen className={styles.treeRootIcon} aria-hidden="true" />
            <button
              className={styles.treeRootLabel}
              type="button"
              aria-expanded={pathExpanded}
              onMouseEnter={() => {
                clearRevealTimer();
                revealTimer.current = setTimeout(() => setPathExpanded(true), 1500);
              }}
              onMouseLeave={() => {
                if (!pathExpanded) clearRevealTimer();
              }}
              onClick={() => setPathExpanded((value) => !value)}
            >
              {rootName}
            </button>
            <span className={styles.treeRootFullPath}>{rootPath ?? rootName}</span>
            <button
              className={styles.treeRootCopy}
              type="button"
              disabled={!rootPath}
              title={copied ? text("Copied", "已复制") : text("Copy path", "复制路径")}
              aria-label={text("Copy path", "复制路径")}
              onClick={() => {
                if (!rootPath) return;
                void copyText(rootPath).then(() => {
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1200);
                });
              }}
            >
              {copied ? <Check /> : <Copy />}
            </button>
          </div>
        ) : null}
        <div className={`${styles.treeToolbarActions} ${!showRootPath ? styles.treeToolbarActionsOnly : ""}`}>
          {!hideSearch && (
          <button
            type="button"
            className={`${styles.iconBtn} ${searchOpen ? styles.iconBtnActive : ""}`}
            onClick={() => searchOpen ? closeSearch() : onSearchOpenChange(true)}
            aria-expanded={searchOpen}
            title={searchOpen ? text("Close search", "关闭搜索") : text("Search", "搜索")}
          >
            <Search />
          </button>
          )}
          {actions}
        </div>
      </div>
      {!hideSearch && (
      <div
        className={`${styles.treeSearchPanel} ${searchOpen ? styles.treeSearchPanelOpen : ""}`}
        aria-hidden={!searchOpen}
      >
        <div className={styles.treeSearchRow}>
          <Search className={styles.treeSearchLeading} aria-hidden="true" />
          <input
            ref={searchRef}
            className={styles.treeSearchInput}
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={text("Search", "搜索")}
            aria-label={text("Search", "搜索")}
            tabIndex={searchOpen ? 0 : -1}
            onKeyDown={(event) => {
              if (event.key === "Escape") closeSearch();
              else if (event.key === "ArrowUp" && resultCount) {
                event.preventDefault();
                onMoveResult(-1);
              } else if (event.key === "ArrowDown" && resultCount) {
                event.preventDefault();
                onMoveResult(1);
              }
            }}
          />
          <div className={styles.treeSearchActions}>
            <span className={styles.treeSearchCount} aria-live="polite">
              {resultCount ? resultIndex + 1 : 0} / {resultCount}
            </span>
            <button className={styles.treeSearchAction} type="button" disabled={!resultCount} tabIndex={searchOpen ? 0 : -1} title={text("Previous match", "上一个匹配项")} onClick={() => onMoveResult(-1)}><ChevronUp /></button>
            <button className={styles.treeSearchAction} type="button" disabled={!resultCount} tabIndex={searchOpen ? 0 : -1} title={text("Next match", "下一个匹配项")} onClick={() => onMoveResult(1)}><ChevronDown /></button>
            <button className={styles.treeSearchAction} type="button" disabled={!query} tabIndex={searchOpen ? 0 : -1} title={text("Clear", "清除")} onClick={() => onQueryChange("")}><X /></button>
          </div>
        </div>
        <div className={styles.treeSearchOptions}>
          <div className={styles.treeSearchMode} role="group" aria-label={text("Search display mode", "搜索显示模式")}>
            <button className={styles.treeSearchOption} type="button" aria-pressed={mode === "filter"} tabIndex={searchOpen ? 0 : -1} onClick={() => onModeChange("filter")}><ListFilter />Filter</button>
            <button className={styles.treeSearchOption} type="button" aria-pressed={mode === "highlight"} tabIndex={searchOpen ? 0 : -1} onClick={() => onModeChange("highlight")}><Highlighter />Highlight</button>
          </div>
          <button className={styles.treeSearchOption} type="button" aria-pressed={fuzzy} tabIndex={searchOpen ? 0 : -1} onClick={() => onFuzzyChange(!fuzzy)}><ScanSearch />Fuzzy</button>
        </div>
      </div>
      )}
    </div>
  );
}
