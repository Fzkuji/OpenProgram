"use client";

import { jsonFetch } from "@/lib/net/fetch-client";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";
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
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const baseDefault = provider.default_base_url
    ? text(`default: ${provider.default_base_url}`, `默认：${provider.default_base_url}`)
    : "";

  async function save() {
    try {
      const result = await jsonFetch<{ error?: string }>(`/api/providers/${encodeURIComponent(provider.id)}/config`, {
        method: "POST",
        body: JSON.stringify({ base_url: value.trim() }),
      });
      if (result.error) { setStatus({ ok: false, text: result.error }); return; }
      setStatus({ ok: true, text: text("Saved", "已保存") });
      onChanged();
    } catch (e) { setStatus({ ok: false, text: (e as Error).message || text("Save failed", "保存失败") }); }
  }

  const id = `base-url-${provider.id}`;
  return (
    <Field>
      <FieldLabel htmlFor={id}>{text("API Base URL", "API 接口地址")}</FieldLabel>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          className="flex-1 font-mono"
          type="text"
          placeholder={provider.default_base_url || "https://..."}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button variant="elevated" onClick={save}>
          {text("Save", "保存")}
        </Button>
      </div>
      {status && !status.ok ? <FieldError>{status.text}</FieldError> : null}
      {status?.ok ? <FieldDescription role="status">{status.text}</FieldDescription> : baseDefault ? <FieldDescription>{baseDefault}</FieldDescription> : null}
    </Field>
  );
}
