"use client";

import { forwardRef, useImperativeHandle, useState } from "react";

import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

import styles from "../settings-page.module.css";

/** Imperative handle so the parent can "click Check" programmatically
 *  (e.g. auto-run after an API key is saved) and await the result. */
export interface ConnectivityHandle {
  run: () => Promise<boolean>;
}


/** Connectivity-check button — POSTs to /api/providers/<id>/validate and
 *  shows ✓ + latency or ✗ + an inline error summary. The full raw
 *  upstream response stays on the hover tooltip for paste-into-bug-
 *  report cases. */
export const Connectivity = forwardRef<ConnectivityHandle, { providerId: string }>(
  function Connectivity({ providerId }, ref) {
  const { text } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ kind: "ok" | "err"; text: string; title?: string } | null>(null);

  async function test(): Promise<boolean> {
    setBusy(true);
    setResult({ kind: "ok", text: "…" });
    try {
      // Auth-only, kind-aware check (NO model call — matches the label below):
      // api-key → probe the key; OAuth (Codex / Copilot) → check the token.
      // This is why a working ChatGPT-Codex login reads ✓ even though a model
      // ping would 400 on an unsupported model — that's a model issue, not auth.
      const r = await fetch(`/api/providers/${encodeURIComponent(providerId)}/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const d = await r.json();
      const status: string = d.status || (d.ok ? "valid" : "unknown");
      // valid / valid_no_balance / valid_model_unavailable all mean the
      // credential authenticates — a pass.
      if (d.ok || status.startsWith("valid")) {
        const title = d.detail || (d.via ? text(`verified via ${d.via}`, `已通过 ${d.via} 验证`) : undefined);
        setResult({ kind: "ok", text: d.latency_ms ? `✓ ${d.latency_ms} ms` : text("✓ valid", "✓ 有效"), title });
        return true;
      }
      const tag = status === "invalid_credential" ? text("✗ invalid key", "✗ key 无效")
        : status === "needs_reauth" ? text("✗ sign in again", "✗ 需重新登录")
        : status === "missing" ? text("✗ not set", "✗ 未设置")
        : `✗ ${status}`;
      setResult({ kind: "err", text: tag, title: d.detail || status });
      return false;
    } catch (e) {
      setResult({ kind: "err", text: "✗", title: (e as Error).message });
      return false;
    } finally {
      setBusy(false);
    }
  }

  // Expose "click Check" to the parent so it can auto-run on key save.
  useImperativeHandle(ref, () => ({ run: test }), [providerId]);

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{text("Connectivity check", "连接检查")}</span>
      </div>
      <div className={styles.detailRow}>
        <span className={styles.modelCountSummary} style={{ flex: 1 }}>
          {text("Checks your API key against the provider's auth endpoint — no model call.", "用提供商的鉴权端点验证 API key —— 不调用任何模型。")}
        </span>
        {result && (
          <span
            className={styles.testResult + " " + (result.kind === "ok" ? styles.ok : styles.err)}
            title={result.title}
            style={{ maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            {result.text}
          </span>
        )}
        <Button size="sm" onClick={() => { void test(); }} disabled={busy}>
          {text("Check", "检查")}
        </Button>
      </div>
    </div>
  );
});
