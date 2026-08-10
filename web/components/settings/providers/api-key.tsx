"use client";

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";
import { normalizeSecretReplacement } from "@/lib/net/secret-replacement";

import styles from "../settings-page.module.css";

/** Single API-key input — masked status plus explicit replacement. Used by
 *  web-search providers (Tavily / Exa / …). LLM providers use <AccountManager>
 *  instead, which manages one-or-many keys as a list. */
export function ApiKey({
  envVar,
  configured,
  onChanged,
  onSaved,
}: {
  envVar: string;
  configured: boolean;
  onChanged: () => void;
  /** Called after a NEW key is actually saved (not on a no-op Save of an
   *  unedited masked field). Lets the parent auto check + fetch models. */
  onSaved?: () => void;
}) {
  const { text } = useTranslation();
  const [replacement, setReplacement] = useState("");
  const [masked, setMasked] = useState("");

  const loadPreview = useCallback(async () => {
    try {
      const r = await fetch(`/api/config/key/${encodeURIComponent(envVar)}`);
      const d = await r.json();
      setMasked(d.has_value ? d.masked || "" : "");
      setReplacement("");
    } catch {
      /* ignore */
    }
  }, [envVar]);

  useEffect(() => {
    loadPreview();
  }, [loadPreview]);

  async function save() {
    const v = normalizeSecretReplacement(replacement, masked);
    if (v === null) return;
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_keys: { [envVar]: v } }),
      });
      const d = await r.json();
      if (d.saved) {
        setReplacement("");
        onChanged();
        loadPreview();
        onSaved?.();
      }
    } catch { /* ignore */ }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>API Key</span>
        <span className={styles.modelCountSummary}>
          {configured ? text("Configured", "已配置") : text("Not set", "未设置")}
        </span>
      </div>
      <div className={styles.detailRow}>
        <Input
          className="flex-1 font-mono"
          type="password"
          placeholder={masked || envVar}
          value={replacement}
          onChange={(e) => setReplacement(e.target.value)}
          autoComplete="new-password"
        />
        <Button size="sm" onClick={save} disabled={!replacement.trim()}>
          {text("Save", "保存")}
        </Button>
      </div>
    </div>
  );
}
