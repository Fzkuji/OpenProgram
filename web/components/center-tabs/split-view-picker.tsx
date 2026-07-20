"use client";

/**
 * SplitViewPicker — "Choose a tab to add to split view".
 *
 * Chrome's model: splitting is an explicit action from the tab context
 * menu, never a drag outcome. The menu entry opens this panel over the
 * center area; picking a row groups the two tabs through the same
 * store action the keyboard menu uses (groupTab).
 *
 * Candidates are every other tab in this window, minus the ones already
 * sharing a split group with the subject — those would be no-ops.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { FileText, Globe, MessageCircle, CirclePlus, X } from "lucide-react";

import { useTranslation } from "@/lib/i18n";
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
  return text("Chat", "会话");
}

function IconFor({ tab }: { tab: CenterTab }) {
  if (tab.kind === "web") return <Globe size={15} aria-hidden="true" />;
  if (tab.kind === "file") return <FileText size={15} aria-hidden="true" />;
  if (tab.kind === "ntp") return <CirclePlus size={15} aria-hidden="true" />;
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
  onClose: () => void;
  onPicked: (accepted: boolean) => void;
}) {
  const { text } = useTranslation();
  const tabs = useCenterTabs((s) => s.tabs);
  const groups = useCenterTabs((s) => s.groups);
  const groupTab = useCenterTabs((s) => s.groupTab);
  const panelRef = useRef<HTMLDivElement>(null);
  const [activeIndex, setActiveIndex] = useState(0);

  const candidates = useMemo(
    () => splitCandidates(tabs, groups, subjectId),
    [tabs, groups, subjectId],
  );

  // Focus the list on open so Up/Down/Enter work immediately.
  useEffect(() => {
    panelRef.current
      ?.querySelector<HTMLButtonElement>("[data-split-option]")
      ?.focus();
  }, []);

  // Escape closes; outside pointerdown closes. Capture phase so nothing
  // downstream can swallow the dismissal first (same rule as the menu).
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    const onOutside = (e: PointerEvent) => {
      const panel = panelRef.current;
      if (panel && e.target instanceof Node && panel.contains(e.target)) return;
      onClose();
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
    // Same commit path as the keyboard menu: the picked tab joins the
    // subject's group (or the two form a new one).
    const subjectGroup = findCenterTabGroup(
      useCenterTabs.getState().groups,
      subjectId,
    );
    const memberIndex = subjectGroup
      ? subjectGroup.memberIds.indexOf(subjectId) + 1
      : 1;
    const accepted = groupTab(tab.id, subjectId, memberIndex, subjectGroup?.id);
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
          className={styles.splitPickerClose}
          aria-label={text("Close", "关闭")}
          title={text("Close", "关闭")}
          onClick={onClose}
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
