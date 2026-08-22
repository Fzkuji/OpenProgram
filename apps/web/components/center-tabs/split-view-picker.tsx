"use client";

/**
 * SplitViewPicker — "Choose a tab to add to split view".
 *
 * Chrome's model: splitting is an explicit action from the tab context
 * menu, never a drag outcome. The menu entry opens this panel over the
 * center area; picking a row groups the two tabs through the same
 * store action the keyboard menu uses (groupTab).
 *
 * Candidates are the other ungrouped tabs in this window. The selection is
 * revalidated against the latest store before commit so a stale row cannot
 * move a tab that another operation has already grouped.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Bookmark, Download, FileText, History, MessageCircle, CirclePlus, TerminalSquare, X } from "lucide-react";

import { ChromeIcon } from "@/components/animated-icons";
import { useTranslation } from "@/lib/i18n";
import { builtinPageLabel } from "./builtin-page-label";
import {
  findCenterTabGroup,
  splitCandidates,
} from "@/lib/state/center-tab-groups";
import { useCenterTabs, type CenterTab } from "@/lib/state/center-tabs-store";
import styles from "./center-tabs.module.css";

/** Secondary line under a candidate's title: origin for web tabs, path
 *  for files, kind for the rest. */
function subtitleOf(
  tab: CenterTab,
  text: ReturnType<typeof useTranslation>["text"],
): string {
  if (tab.kind === "web") {
    try {
      return new URL(tab.url ?? "").host;
    } catch {
      return tab.url ?? "";
    }
  }
  if (tab.kind === "file") return tab.path ?? "";
  if (tab.kind === "ntp") return text("New tab", "新标签页");
  if (tab.kind === "builtin") return builtinPageLabel(tab.page, text);
  return text("Chat", "会话");
}

function IconFor({ tab }: { tab: CenterTab }) {
  if (tab.kind === "web") return <ChromeIcon size={15} aria-hidden="true" />;
  if (tab.kind === "file") return <FileText size={15} aria-hidden="true" />;
  if (tab.kind === "ntp") return <CirclePlus size={15} aria-hidden="true" />;
  if (tab.kind === "builtin") {
    if (tab.page === "files") return <FileText size={15} aria-hidden="true" />;
    if (tab.page === "history") return <History size={15} aria-hidden="true" />;
    if (tab.page === "downloads") return <Download size={15} aria-hidden="true" />;
    if (tab.page === "browser") return <ChromeIcon size={15} aria-hidden="true" />;
    if (tab.page === "terminal" || tab.page === "claude") return <TerminalSquare size={15} aria-hidden="true" />;
    return <Bookmark size={15} aria-hidden="true" />;
  }
  return <MessageCircle size={15} aria-hidden="true" />;
}

export function SplitViewPicker({
  subjectId,
  titleOf,
  onClose,
  onPicked,
}: {
  subjectId: string;
  /** Reuse the strip's label resolution so titles match the tabs. */
  titleOf: (tab: CenterTab) => string;
  onClose: (reason: "escape" | "close-button" | "outside") => void;
  onPicked: (accepted: boolean) => void;
}) {
  const { text } = useTranslation();
  const tabs = useCenterTabs((s) => s.tabs);
  const groups = useCenterTabs((s) => s.groups);
  const panelRef = useRef<HTMLDivElement>(null);
  const [activeIndex, setActiveIndex] = useState(0);

  const candidates = useMemo(
    () => splitCandidates(tabs, groups, subjectId),
    [tabs, groups, subjectId],
  );

  // Keep focus inside the modal. With no candidate, the close button is the
  // only interactive control and receives initial focus.
  useEffect(() => {
    const panel = panelRef.current;
    if (!panel || panel.contains(document.activeElement)) return;
    const target =
      panel.querySelector<HTMLButtonElement>("[data-split-option]") ??
      panel.querySelector<HTMLButtonElement>("[data-split-close]");
    target?.focus();
  }, [candidates.length]);

  // Escape closes; outside pointerdown closes. Capture phase so nothing
  // downstream can swallow the dismissal first (same rule as the menu).
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose("escape");
      }
    };
    const onOutside = (e: PointerEvent) => {
      const panel = panelRef.current;
      if (panel && e.target instanceof Node && panel.contains(e.target)) return;
      onClose("outside");
    };
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("pointerdown", onOutside, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      document.removeEventListener("pointerdown", onOutside, true);
    };
  }, [onClose]);

  function focusOption(index: number) {
    const options = panelRef.current?.querySelectorAll<HTMLButtonElement>(
      "[data-split-option]",
    );
    if (!options || options.length === 0) return;
    const next = (index + options.length) % options.length;
    setActiveIndex(next);
    options[next].focus();
  }

  function choose(tab: CenterTab) {
    const latestState = useCenterTabs.getState();
    const subjectExists = latestState.tabs.some((item) => item.id === subjectId);
    const targetIsCurrent = splitCandidates(
      latestState.tabs,
      latestState.groups,
      subjectId,
    ).some((item) => item.id === tab.id);
    if (!subjectExists || !targetIsCurrent) {
      onPicked(false);
      return;
    }

    const subjectGroup = findCenterTabGroup(
      latestState.groups,
      subjectId,
    );
    const memberIndex = subjectGroup
      ? subjectGroup.memberIds.indexOf(subjectId) + 1
      : 1;
    const accepted = latestState.groupTab(
      tab.id,
      subjectId,
      memberIndex,
      subjectGroup?.id,
    );
    onPicked(accepted);
  }

  return (
    <div
      ref={panelRef}
      className={styles.splitPicker}
      role="dialog"
      aria-modal="true"
      aria-label={text(
        "Choose a tab to add to split view",
        "选择要加入分屏的标签页",
      )}
      onKeyDown={(e) => {
        if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
        e.preventDefault();
        focusOption(activeIndex + (e.key === "ArrowDown" ? 1 : -1));
      }}
    >
      <div className={styles.splitPickerHeader}>
        <span className={styles.splitPickerTitle}>
          {text("Choose a tab to add to split view", "选择要加入分屏的标签页")}
        </span>
        <button
          type="button"
          data-split-close
          className={styles.splitPickerClose}
          aria-label={text("Close", "关闭")}
          title={text("Close", "关闭")}
          onClick={() => onClose("close-button")}
        >
          <X size={15} />
        </button>
      </div>
      {candidates.length === 0 ? (
        <p className={styles.splitPickerEmpty}>
          {text("No other tabs to split with", "没有可用于分屏的其他标签页")}
        </p>
      ) : (
        <div role="listbox" aria-label={text("Open tabs", "打开的标签")}>
          {candidates.map((tab, index) => (
            <button
              key={tab.id}
              type="button"
              role="option"
              aria-selected={index === activeIndex}
              data-split-option
              tabIndex={index === activeIndex ? 0 : -1}
              className={styles.splitPickerOption}
              onFocus={() => setActiveIndex(index)}
              onClick={() => choose(tab)}
            >
              <span className={styles.splitPickerIcon} aria-hidden="true">
                <IconFor tab={tab} />
              </span>
              <span className={styles.splitPickerText}>
                <span className={styles.splitPickerName}>{titleOf(tab)}</span>
                <span className={styles.splitPickerMeta}>
                  {subtitleOf(tab, text)}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
