"use client";

import { jsonFetch } from "@/lib/net/fetch-client";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import styles from "../providers/provider-pane.module.css";
import { useTranslation } from "@/lib/i18n";

export function SearchConnectivity({
  providerId,
  disabled,
  needsKey = true,
}: {
  providerId: string;
  disabled: boolean;
  /** False for keyless backends, so the copy does not mention an API key. */
  needsKey?: boolean;
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
      const d = await jsonFetch<{ ok?: boolean; latency_ms?: number; result_count?: number; error?: string }>(
        `/api/search-providers/${encodeURIComponent(providerId)}/test`,
        { method: "POST", body: JSON.stringify({}) },
      );
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
    <div className="flex flex-col gap-[10px]">
      <div className={styles.detailSectionTitle}>
        <span>{text("Connectivity check", "连接检查")}</span>
      </div>
      <div className={`${styles.detailRow} flex gap-[10px] items-center`}>
        <span className={styles.modelCountSummary} style={{ flex: 1 }}>
          {disabled
            ? needsKey
              ? text("Add an API key above first.", "请先在上方添加 API key。")
              : text("This backend is not available yet.", "这个后端暂不可用。")
            : needsKey
            ? text("Runs a tiny live query to validate the API key.", "运行一个小型实时查询来验证 API key。")
            : text("Runs a tiny live query to confirm this backend responds.", "运行一个小型实时查询，确认该后端可用。")}
        </span>
        {result && (
          <span
            className={
              styles.testResult +
              " " +
              (result.kind === "ok" ? styles.ok : styles.err)
            }
            data-tip={result.title}
          >
            {result.text}
          </span>
        )}
        <Button
          onClick={test}
          disabled={busy || disabled}
        >
          {text("Check", "检查")}
        </Button>
      </div>
    </div>
  );
}
