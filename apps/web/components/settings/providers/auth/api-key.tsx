"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { SecretInput } from "@/components/ui/secret-input";
import { useTranslation } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import { errorDetailText } from "@/lib/net/error-text";
import { normalizeSecretReplacement } from "@/lib/net/auth/secret-replacement";

/** Single API-key input — masked status plus explicit replacement. Used by
 *  web-search providers (Tavily / Exa / …). LLM providers use the
 *  Credentials section instead, which manages one-or-many keys as a list. */
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
      const d = await r.json() as { has_value?: boolean; masked?: string };
      setMasked(d.has_value ? d.masked || "" : "");
      setReplacement("");
    } catch (error) {
      console.warn(`The saved ${envVar} could not be read`, error);
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
      const d = await r.json() as { saved?: boolean; error?: unknown };
      if (d.saved) {
        setReplacement("");
        onChanged();
        loadPreview();
        onSaved?.();
      } else {
        showToast(errorDetailText(d.error, text("Could not save the key.", "无法保存密钥。")), { tone: "error" });
      }
    } catch {
      showToast(text("Could not save the key.", "无法保存密钥。"), { tone: "error" });
    }
  }

  const id = `api-key-${envVar}`;
  return (
    <Field>
      <div className="flex items-center justify-between gap-2">
        <FieldLabel htmlFor={id}>API Key</FieldLabel>
        <Badge variant={configured ? "success" : "secondary"} dot>
          {configured ? text("Configured", "已配置") : text("Not set", "未设置")}
        </Badge>
      </div>
      <div className="flex items-center gap-2">
        <SecretInput
          id={id}
          className="flex-1"
          placeholder={masked || envVar}
          value={replacement}
          onChange={(e) => setReplacement(e.target.value)}
          autoComplete="new-password"
        />
        <Button variant="elevated" onClick={save} disabled={!replacement.trim()}>
          {text("Save", "保存")}
        </Button>
      </div>
    </Field>
  );
}
