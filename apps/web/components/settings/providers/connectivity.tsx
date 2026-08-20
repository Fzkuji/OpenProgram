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

function isUsableValid(status: string, via?: string, kind?: string): boolean {
  if (status !== "valid") return false;
  const v = via || "";
  if (v.startsWith("GET /key") || v.startsWith("POST ")) return true;
  if (kind === "oauth" || v === "CredentialProvider") return true;
  return false;
}


/** Connectivity-check button — POSTs to /api/providers/<id>/validate and
 *  shows ✓ + latency or ✗ + an inline error summary. The full raw
 *  upstream response stays on the hover tooltip for paste-into-bug-
 *  report cases. */
export const Connectivity = forwardRef<ConnectivityHandle, { providerId: string }>(
  function Connectivity({ providerId }, ref) {
  const { text } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ kind: "ok" | "err" | "warn" | "info"; text: string; title?: string } | null>(null);

  async function test(): Promise<boolean> {
    setBusy(true);
    setResult({ kind: "info", text: "…" });
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(providerId)}/validate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const d = await r.json();
      const status: string = d.status || (d.ok ? "valid" : "unknown");
      const via: string = d.via || "";
      const title = d.detail || (via ? text(`verified via ${via}`, `已通过 ${via} 验证`) : undefined);
      if (isUsableValid(status, via, d.kind)) {
        setResult({ kind: "ok", text: d.latency_ms ? `✓ ${d.latency_ms} ms` : text("✓ valid", "✓ 有效"), title });
        return true;
      }
      if (status === "valid_no_balance" || status === "billing_blocked") {
        setResult({ kind: "err", text: text("✗ out of credits", "✗ 欠费停用"), title });
        return false;
      }
      if (status === "valid_model_unavailable") {
        setResult({ kind: "warn", text: text("model unavailable", "模型不可用"), title });
        return false;
      }
      if (status === "valid" || (status === "unknown" && via.startsWith("GET "))) {
        setResult({ kind: "info", text: text("key accepted", "密钥已接受"), title });
        return false;
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
          {text("Confirms the key is usable now — a cheap auth check, plus a tiny completion ping when the provider has no billing endpoint.", "确认密钥现在可用：先做廉价鉴权，提供商没有余额端点时再发一次极小的补全探测。")}
        </span>
        {result && (
          <span
            className={
              styles.testResult + " " + (
                result.kind === "ok" ? styles.ok
                : result.kind === "err" ? styles.err
                : result.kind === "warn" ? styles.warn
                : styles.info
              )
            }
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
