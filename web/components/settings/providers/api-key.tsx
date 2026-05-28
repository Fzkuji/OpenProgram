"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import styles from "../settings-page.module.css";

/** API-key input — mask / reveal / save against /api/config.
 *
 *  Exported because search-providers-section.tsx reuses the same
 *  widget for its own provider keys. */
export function ApiKey({
  envVar,
  configured,
  onChanged,
}: {
  envVar: string;
  configured: boolean;
  onChanged: () => void;
}) {
  const { text } = useTranslation();
  const [value, setValue] = useState("");
  const [state, setState] = useState<"empty" | "masked" | "editing" | "revealed">("empty");
  const [showText, setShowText] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const loadPreview = useCallback(async () => {
    try {
      const r = await fetch(`/api/config/key/${encodeURIComponent(envVar)}`);
      const d = await r.json();
      if (d.has_value) {
        setValue(d.masked || "");
        setState("masked");
        setShowText(false);
      } else {
        setValue("");
        setState("empty");
      }
    } catch {
      /* ignore */
    }
  }, [envVar]);

  useEffect(() => {
    loadPreview();
  }, [loadPreview]);

  async function toggleVisibility() {
    if (state === "empty" || state === "editing") {
      setShowText((v) => !v);
      return;
    }
    if (state === "masked") {
      try {
        const r = await fetch(`/api/config/key/${encodeURIComponent(envVar)}?reveal=1`);
        const d = await r.json();
        if (d.has_value) {
          setValue(d.value || "");
          setShowText(true);
          setState("revealed");
        }
      } catch { /* ignore */ }
    } else {
      try {
        const r = await fetch(`/api/config/key/${encodeURIComponent(envVar)}`);
        const d = await r.json();
        if (d.has_value) {
          setValue(d.masked || "");
          setShowText(false);
          setState("masked");
        }
      } catch { /* ignore */ }
    }
  }

  function onInput(v: string) {
    if (state === "masked" || state === "revealed") {
      setValue("");
      setShowText(false);
      setState("editing");
      return;
    }
    setValue(v);
  }

  async function save() {
    const v = value.trim();
    if (!v || v.indexOf("...") >= 0) return;
    try {
      const r = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_keys: { [envVar]: v } }),
      });
      const d = await r.json();
      if (d.saved) {
        setValue("");
        if (inputRef.current) inputRef.current.placeholder = text(`${envVar} (saved)`, `${envVar}（已保存）`);
        onChanged();
        loadPreview();
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
          ref={inputRef}
          className="h-9 flex-1 font-mono"
          type={showText ? "text" : "password"}
          placeholder={envVar}
          value={value}
          onChange={(e) => onInput(e.target.value)}
        />
        <button
          className={styles.iconBtn}
          title={text("Show/hide", "显示/隐藏")}
          onClick={toggleVisibility}
        >
          {showText ? "🙈" : "👁"}
        </button>
        <Button size="sm" onClick={save}>
          {text("Save", "保存")}
        </Button>
      </div>
    </div>
  );
}
