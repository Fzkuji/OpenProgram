"use client";

import { Eye, EyeOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import { PoolControls } from "./pool-controls";
import styles from "../settings-page.module.css";

/** API-key input — mask / reveal / save against /api/config.
 *
 *  For LLM providers (pass ``providerId``) the section also hosts a nested,
 *  collapsed "extra keys for rotation" block, so a provider's keys live in one
 *  place instead of two competing sections. Omit ``providerId`` (web-search
 *  providers reuse this widget) to render just the single-key field.
 *
 *  Exported because search-providers-section.tsx reuses the same widget. */
export function ApiKey({
  envVar,
  configured,
  onChanged,
  onSaved,
  providerId,
}: {
  envVar: string;
  configured: boolean;
  onChanged: () => void;
  /** Called after a NEW key is actually saved (not on a no-op Save of an
   *  unedited masked field). Lets the parent auto check + fetch models. */
  onSaved?: () => void;
  /** LLM provider id — when set, render the nested rotation-keys block. */
  providerId?: string;
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
    // Only save a genuinely user-entered key. When the field is showing
    // the server's masked preview ("••••") or the revealed real key and
    // the user clicked Save WITHOUT editing, ``value`` is that preview —
    // NOT a new key. Saving it would overwrite the real key with the
    // mask (the "•" bullets aren't even ASCII, which then blows up the
    // fetch with a UnicodeEncodeError). So bail unless we're in an edit.
    if (state === "masked" || state === "revealed") return;
    // Defensive content guards: never persist a masked/elided value even
    // if the state machine somehow disagrees.
    if (!v || v.indexOf("...") >= 0 || v.includes("•") || /[^\x20-\x7e]/.test(v)) {
      return;
    }
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
          ref={inputRef}
          className="flex-1 font-mono"
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
          {/* Crossfade + scale between the open eye (masked → "reveal")
              and the slashed eye (revealed → "hide"). Both icons are
              stacked; toggling animates opacity/scale so the swap morphs
              instead of snapping. */}
          <span style={{ position: "relative", display: "inline-block", width: 16, height: 16 }}>
            <Eye
              size={16}
              strokeWidth={1.8}
              style={{
                position: "absolute",
                inset: 0,
                transition: "opacity 180ms ease, transform 180ms ease",
                opacity: showText ? 0 : 1,
                transform: showText ? "scale(0.5)" : "scale(1)",
              }}
            />
            <EyeOff
              size={16}
              strokeWidth={1.8}
              style={{
                position: "absolute",
                inset: 0,
                transition: "opacity 180ms ease, transform 180ms ease",
                opacity: showText ? 1 : 0,
                transform: showText ? "scale(1)" : "scale(0.5)",
              }}
            />
          </span>
        </button>
        <Button size="sm" onClick={save}>
          {text("Save", "保存")}
        </Button>
      </div>
      {/* Extra keys for rotation live inside this same section (LLM providers
          only), so all of a provider's keys are in one place. */}
      {providerId && <PoolControls providerId={providerId} />}
    </div>
  );
}
