"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import styles from "../settings-page.module.css";
import type { Provider } from "./types";

/** Optional API-base-URL override — used for self-hosted OpenAI-
 *  compatible endpoints (LiteLLM, OpenRouter, vLLM, etc.). Saving an
 *  empty string falls back to ``default_base_url``. */
export function BaseUrl({
  provider,
  onChanged,
}: {
  provider: Provider;
  onChanged: () => void;
}) {
  const { text } = useTranslation();
  const [value, setValue] = useState(provider.base_url || "");
  const baseDefault = provider.default_base_url
    ? text(`default: ${provider.default_base_url}`, `默认：${provider.default_base_url}`)
    : "";

  async function save() {
    try {
      await fetch(`/api/providers/${encodeURIComponent(provider.id)}/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_url: value.trim() }),
      });
      onChanged();
    } catch { /* ignore */ }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>API Base URL</span>
        <span className={styles.modelCountSummary}>{baseDefault}</span>
      </div>
      <div className={styles.detailRow}>
        <Input
          className="flex-1 font-mono"
          type="text"
          placeholder={provider.default_base_url || "https://..."}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button size="sm" onClick={save}>
          {text("Save", "保存")}
        </Button>
      </div>
    </div>
  );
}
