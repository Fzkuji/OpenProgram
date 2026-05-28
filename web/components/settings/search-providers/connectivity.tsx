"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import styles from "../settings-page.module.css";
import { useTranslation } from "@/lib/i18n";

export function SearchConnectivity({
  providerId,
  disabled,
}: {
  providerId: string;
  disabled: boolean;
}) {
  const { text } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    kind: "ok" | "err";
    text: string;
    title?: string;
  } | null>(null);

  // Reset state when the user switches to a different provider —
  // otherwise the previous "✓ 200 ms" stays visible on the new panel.
  useEffect(() => {
    setResult(null);
  }, [providerId]);

  async function test() {
    setBusy(true);
    setResult({ kind: "ok", text: "…" });
    try {
      const r = await fetch(
        `/api/search-providers/${encodeURIComponent(providerId)}/test`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        },
      );
      const d = await r.json();
      if (d.ok) {
        setResult({
          kind: "ok",
          text: `✓ ${d.latency_ms || 0} ms`,
          title:
            typeof d.result_count === "number"
              ? text(
                  `Returned ${d.result_count} result${d.result_count === 1 ? "" : "s"}`,
                  `返回 ${d.result_count} 条结果`,
                )
              : undefined,
        });
      } else {
        setResult({ kind: "err", text: text("✗ failed", "✗ 失败"), title: d.error });
      }
    } catch (e) {
      setResult({ kind: "err", text: "✗", title: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{text("Connectivity check", "连接检查")}</span>
      </div>
      <div className={styles.detailRow}>
        <span className={styles.modelCountSummary} style={{ flex: 1 }}>
          {text("Runs a tiny live query to validate the API key.", "运行一个小型实时查询来验证 API key。")}
        </span>
        {result && (
          <span
            className={
              styles.testResult +
              " " +
              (result.kind === "ok" ? styles.ok : styles.err)
            }
            title={result.title}
          >
            {result.text}
          </span>
        )}
        <Button
          size="sm"
          onClick={test}
          disabled={busy || disabled}
          title={disabled ? text("Configure the API key first", "请先配置 API key") : undefined}
        >
          {text("Check", "检查")}
        </Button>
      </div>
    </div>
  );
}
