"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Highlighter,
  ListFilter,
  ScanSearch,
  Search,
  X,
} from "lucide-react";

import { SolarIcon } from "@/components/solar-icons";
import { copyText } from "@/lib/clipboard";
import { useTranslation } from "@/lib/i18n";
import { matchingIndexes } from "./explorer-search";
import { HoverTip } from "@/components/ui/tooltip";
import { Button } from "@/components/ui/button";
import { Toggle } from "@/components/ui/toggle";
import styles from "../files-panel.module.css";

export type ExplorerSearchMode = "filter" | "highlight";

export { copyText } from "@/lib/clipboard";

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
  pathNavigation,
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
  pathNavigation?: ReactNode;
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
      {pathNavigation ? <div className={styles.filePathRow}>{leading}{pathNavigation}</div> : null}
      <div
        className={`${styles.treeToolbar} ${showRootPath && pathExpanded ? styles.treeToolbarExpanded : ""}`}
        onMouseLeave={() => {
          clearRevealTimer();
          setPathExpanded(false);
        }}
      >
        {!pathNavigation ? leading : null}
        {showRootPath && !pathNavigation ? (
          <div className={styles.treeRootPath}>
            <SolarIcon name="folder-open" size={16} motionPreset="none" className={`${styles.treeRootIcon} text-[var(--folder-color)]`} aria-hidden="true" />
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
            <HoverTip label={copied ? text("Copied", "已复制") : text("Copy path", "复制路径")}><Button
              className={pathExpanded ? undefined : "hidden"}
              type="button"
              variant="ghost"
              size="icon-sm"
              disabled={!rootPath}

              aria-label={text("Copy path", "复制路径")}
              onClick={() => {
                if (!rootPath) return;
                void copyText(rootPath).then(success => {
                  if (!success) return;
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1200);
                });
              }}
            >
              {copied ? <Check /> : <Copy />}
            </Button></HoverTip>
          </div>
        ) : null}
        <div className={`${styles.treeToolbarActions} ${!showRootPath ? "ml-[auto]" : ""}`}>
          {!hideSearch && (
          <HoverTip label={searchOpen ? text("Close search", "关闭搜索") : text("Search", "搜索")}><Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={() => searchOpen ? closeSearch() : onSearchOpenChange(true)}
            aria-expanded={searchOpen}
            aria-label={searchOpen ? text("Close search", "关闭搜索") : text("Search", "搜索")}
          >
            <Search />
          </Button></HoverTip>
          )}
          {actions}
        </div>
      </div>
      {!hideSearch && (
      <div
        className={`${styles.treeSearchPanel} ${searchOpen ? styles.treeSearchPanelOpen : ""}`}
        aria-hidden={!searchOpen}
      >
        <div className="relative w-full min-w-0 h-(--ui-button-h) flex items-center">
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
          <div className="absolute right-[4px] flex items-center gap-[1px]">
            <span className={styles.treeSearchCount} aria-live="polite">
              {resultCount ? resultIndex + 1 : 0} / {resultCount}
            </span>
            <HoverTip label={text("Previous match", "上一个匹配项")}><Button variant="ghost" size="icon-xs" type="button" disabled={!resultCount} tabIndex={searchOpen ? 0 : -1} aria-label={text("Previous match", "上一个匹配项")} onClick={() => onMoveResult(-1)}><ChevronUp /></Button></HoverTip>
            <HoverTip label={text("Next match", "下一个匹配项")}><Button variant="ghost" size="icon-xs" type="button" disabled={!resultCount} tabIndex={searchOpen ? 0 : -1} aria-label={text("Next match", "下一个匹配项")} onClick={() => onMoveResult(1)}><ChevronDown /></Button></HoverTip>
            <HoverTip label={text("Clear", "清除")}><Button variant="ghost" size="icon-xs" type="button" disabled={!query} tabIndex={searchOpen ? 0 : -1} aria-label={text("Clear", "清除")} onClick={() => onQueryChange("")}><X /></Button></HoverTip>
          </div>
        </div>
        <div className="h-(--ui-button-h-sm) mt-[4px] flex items-center justify-between gap-[8px]">
          <div className="flex items-center gap-[2px]" role="group" aria-label={text("Search display mode", "搜索显示模式")}>
            <Toggle size="sm" pressed={mode === "filter"} tabIndex={searchOpen ? 0 : -1} onPressedChange={() => onModeChange("filter")}><ListFilter />Filter</Toggle>
            <Toggle size="sm" pressed={mode === "highlight"} tabIndex={searchOpen ? 0 : -1} onPressedChange={() => onModeChange("highlight")}><Highlighter />Highlight</Toggle>
          </div>
          <Toggle size="sm" pressed={fuzzy} tabIndex={searchOpen ? 0 : -1} onPressedChange={onFuzzyChange}><ScanSearch />Fuzzy</Toggle>
        </div>
      </div>
      )}
    </div>
  );
}
