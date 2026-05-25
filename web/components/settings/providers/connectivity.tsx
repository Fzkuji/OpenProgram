"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";

import styles from "../settings-page.module.css";

/** Connectivity-check button — POSTs to /api/providers/<id>/test and
 *  shows ✓ + latency or ✗ + error tooltip. Lets the user verify the
 *  API key + base URL are correct without leaving the settings page. */
export function Connectivity({ providerId }: { providerId: string }) {
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
          title: d.model ? `Tested with ${d.model}` : undefined,
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
          Validates API key + base URL with a tiny PING.
        </span>
        {result && (
          <span className={styles.testResult + " " + (result.kind === "ok" ? styles.ok : styles.err)} title={result.title}>
            {result.text}
          </span>
        )}
        <Button size="sm" onClick={test} disabled={busy}>
          Check
        </Button>
      </div>
    </div>
  );
}
