"use client";

/** The line under a Program's title that says why it is not plainly loaded. */
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import { keptVersionFailure, type ProgramExplorerEntry } from "./programs-catalog";
import styles from "./programs-page.module.css";

export function ProgramLoadState({ entry }: { entry: ProgramExplorerEntry }) {
  const { text } = useTranslation();
  const [showFullError, setShowFullError] = useState(false);
  const kept = keptVersionFailure(entry, text);
  const message = kept
    ? (showFullError ? kept.fullError : kept.message)
    : entry.load_error || entry.load_note;
  return (
    <div className={`${styles.loadState} ${entry.load_state === "error" ? styles.loadStateError : ""}`} role="status" data-testid="programs-load-state">
      <strong>{kept ? kept.title : entry.load_state === "error"
        ? text("Failed to load", "加载失败")
        : entry.load_state === "pending"
          ? text("Not callable yet", "尚不可调用")
          : text("Uncommitted changes", "有未提交的修改")}</strong>
      {message ? <p>{message}</p> : null}
      {kept && !showFullError ? (
        <Button type="button" variant="link" size="xs" onClick={() => setShowFullError(true)}>
          {text("See full error", "查看完整错误")}
        </Button>
      ) : null}
    </div>
  );
}
