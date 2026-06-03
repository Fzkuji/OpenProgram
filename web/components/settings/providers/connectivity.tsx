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


/** Classify a backend ``test_provider`` error string into a short
 *  human-readable summary the connectivity check can show inline.
 *
 *  The raw ``error`` field shape is ``HTTP <code>: <upstream body>``
 *  (mirroring ``test_provider`` in ``webui/_model_catalog.py``). The
 *  upstream body is usually JSON; the most actionable signal is the
 *  HTTP status (401 → bad key, 402 → no balance, 429 → rate limit,
 *  403 → CF block, …). We surface a short tag + the upstream's own
 *  ``message`` field when present, so the user doesn't have to hover
 *  over a generic "✗ failed" to learn that DeepSeek wants them to
 *  top up. The full untouched error stays available as a tooltip. */
function summarizeError(raw: string | undefined): { short: string; tooltip: string } {
  const tooltip = raw || "Unknown error";
  if (!raw) return { short: "✗ failed", tooltip };

  // Pull out the status code if the upstream wrapper formatted as "HTTP <n>: ...".
  const m = raw.match(/^HTTP (\d+):\s*([\s\S]*)$/);
  const code = m ? Number(m[1]) : NaN;
  const body = m ? m[2] : raw;

  // Best-effort JSON parse: most providers return {"error":{"message":"...","type":"..."}}.
  let upstreamMsg = "";
  try {
    const j = JSON.parse(body);
    upstreamMsg =
      j?.error?.message ||
      j?.message ||
      j?.error_message ||
      "";
  } catch {
    // Body isn't JSON — leave upstreamMsg empty; raw stays in tooltip.
  }

  const tag = (() => {
    switch (code) {
      case 401:
      case 403:
        return "✗ auth";
      case 402:
        return "✗ no balance";
      case 404:
        return "✗ not found";
      case 408:
      case 504:
        return "✗ timeout";
      case 429:
        return "✗ rate limited";
      case 500:
      case 502:
      case 503:
        return "✗ upstream";
      default:
        return code ? `✗ HTTP ${code}` : "✗ failed";
    }
  })();

  const short = upstreamMsg ? `${tag}: ${upstreamMsg}` : tag;
  return { short, tooltip };
}

/** Connectivity-check button — POSTs to /api/providers/<id>/test and
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
      const r = await fetch(`/api/providers/${encodeURIComponent(providerId)}/test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const d = await r.json();
      if (d.ok) {
        // ``note`` present → the key authenticated but the model pinged is
        // temporarily unavailable (rate-limited / dead upstream / data
        // policy). Still a pass — the credential works — but say so.
        if (d.note) {
          setResult({
            kind: "ok",
            text: text("✓ key valid · model unavailable", "✓ key 有效 · 模型暂不可用"),
            title: d.note,
          });
          return true;
        }
        // ``via`` → confirmed against a model-independent auth endpoint
        // (e.g. GET /key, GET /models). ``model`` → an inference ping was
        // used (caller named a specific model). Either way it's a pass.
        const title = d.via
          ? text(`Key verified via ${d.via}`, `已通过 ${d.via} 验证 key`)
          : d.model
            ? text(`Tested with ${d.model}`, `已使用 ${d.model} 测试`)
            : undefined;
        setResult({ kind: "ok", text: `✓ ${d.latency_ms || 0} ms`, title });
        return true;
      }
      const { short, tooltip } = summarizeError(d.error);
      setResult({ kind: "err", text: short, title: tooltip });
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
