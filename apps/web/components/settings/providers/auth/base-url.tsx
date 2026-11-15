"use client";

import { jsonFetch } from "@/lib/net/fetch-client";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import styles from "../provider-pane.module.css";
import type { Provider } from "../types";

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
  const [status, setStatus] = useState<string | null>(null);
  const baseDefault = provider.default_base_url
    ? text(`default: ${provider.default_base_url}`, `默认：${provider.default_base_url}`)
    : "";

  async function save() {
    try {
      const result = await jsonFetch<{ error?: string }>(`/api/providers/${encodeURIComponent(provider.id)}/config`, {
        method: "POST",
        body: JSON.stringify({ base_url: value.trim() }),
      });
      if (result.error) { setStatus(result.error); return; }
      setStatus(text("Saved", "已保存"));
      onChanged();
    } catch (e) { setStatus((e as Error).message || text("Save failed", "保存失败")); }
  }

  return (
    <div className="flex flex-col gap-[10px]">
      <div className={styles.detailSectionTitle}>
        <span>API Base URL</span>
        <span className={styles.modelCountSummary}>{baseDefault}</span>
      </div>
      <div className={`${styles.detailRow} flex gap-[10px] items-center`}>
        <Input
          className="flex-1 font-mono"
          type="text"
          placeholder={provider.default_base_url || "https://..."}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button onClick={save}>
          {text("Save", "保存")}
        </Button>
      </div>
      {status && <span role="status">{status}</span>}
    </div>
  );
}
