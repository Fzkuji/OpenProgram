"use client";

import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { useTranslation } from "@/lib/i18n";
import styles from "../approval/approval-mode.module.css";

/** Shared presentation for the command and its exact JSON arguments. */
export function OperationCode({ value, language }: { value: string; language: string }) {
  const { text } = useTranslation();
  const [status, setStatus] = useState<"idle" | "copied" | "error">("idle");
  useEffect(() => {
    setStatus("idle");
  }, [value]);
  useEffect(() => {
    if (status === "idle") return;
    const timer = setTimeout(() => setStatus("idle"), 2000);
    return () => clearTimeout(timer);
  }, [status]);
  const label = status === "copied" ? text("Copied", "已复制")
    : status === "error" ? text("Copy failed; select the code to copy", "复制失败，请选中代码复制")
      : text("Copy code", "复制代码");
  return (
    <div className={styles.codeBlock}>
      <pre className={styles.codeContent}><code className={`language-${language}`}>{value}</code></pre>
      <div className={styles.codeTools}>
        <span>{language}</span>
        <button type="button" className={styles.copyButton} title={label} aria-label={label}
          onClick={async () => {
            try { await navigator.clipboard.writeText(value); setStatus("copied"); }
            catch { setStatus("error"); }
          }}>
          {status === "copied" ? <Check size={16} /> : <Copy size={16} />}
        </button>
      </div>
      <span role="status" className="sr-only">{status === "idle" ? "" : label}</span>
    </div>
  );
}
