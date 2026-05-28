"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

import styles from "../settings-page.module.css";

/** Connectivity-check button — POSTs to /api/providers/<id>/test and
 *  shows ✓ + latency or ✗ + error tooltip. Lets the user verify the
 *  API key + base URL are correct without leaving the settings page. */
export function Connectivity({ providerId }: { providerId: string }) {
  const { text } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ kind: "ok" | "err"; text: string; title?: string } | null>(null);

  async function test() {
    setBusy(true);
    setResult({ kind: "ok", text: "…" });
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(providerId)}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const d = await r.json();
      if (d.ok) {
        setResult({
          kind: "ok",
          text: `✓ ${d.latency_ms || 0} ms`,
          title: d.model ? text(`Tested with ${d.model}`, `已使用 ${d.model} 测试`) : undefined,
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
          {text("Validates API key + base URL with a tiny PING.", "用小型 PING 验证 API key 和 base URL。")}
        </span>
        {result && (
          <span className={styles.testResult + " " + (result.kind === "ok" ? styles.ok : styles.err)} title={result.title}>
            {result.text}
          </span>
        )}
        <Button size="sm" onClick={test} disabled={busy}>
          {text("Check", "检查")}
        </Button>
      </div>
    </div>
  );
}
