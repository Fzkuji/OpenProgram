"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import styles from "../settings-page.module.css";

export function SearchConnectivity({
  providerId,
  disabled,
}: {
  providerId: string;
  disabled: boolean;
}) {
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
              ? `Returned ${d.result_count} result${d.result_count === 1 ? "" : "s"}`
              : undefined,
        });
      } else {
        setResult({ kind: "err", text: "✗ failed", title: d.error });
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
        <span>Connectivity check</span>
      </div>
      <div className={styles.detailRow}>
        <span className={styles.modelCountSummary} style={{ flex: 1 }}>
          Runs a tiny live query to validate the API key.
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
          title={disabled ? "Configure the API key first" : undefined}
        >
          Check
        </Button>
      </div>
    </div>
  );
}

