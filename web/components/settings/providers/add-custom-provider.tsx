"use client";

import { useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import styles from "../settings-page.module.css";
import { useTranslation } from "@/lib/i18n";

/** "Add custom provider" — a small inline form under the sidebar search.
 *  Creates a config-only (tier-3) OpenAI-compatible provider via
 *  POST /api/providers/custom. On success calls onCreated(id) so the parent
 *  reloads the list and selects the new provider. */
export function AddCustomProvider({ onCreated }: { onCreated: (id: string) => void }) {
  const { text } = useTranslation();
  const [open, setOpen] = useState(false);
  const [id, setId] = useState("");
  const [label, setLabel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setId("");
    setLabel("");
    setBaseUrl("");
    setError(null);
    setOpen(false);
  }

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      const r = await fetch("/api/providers/custom", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: id.trim(), label: label.trim(), base_url: baseUrl.trim() }),
      });
      const d = await r.json();
      if (!d.ok) {
        setError(d.error || text("Failed to create provider", "创建 Provider 失败"));
        return;
      }
      const newId = d.id as string;
      reset();
      onCreated(newId);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <Button
        variant="outline"
        size="sm"
        style={{ margin: "8px 8px 0", width: "calc(100% - 16px)" }}
        onClick={() => setOpen(true)}
      >
        <Plus />
        {text("Add custom provider", "添加自定义 Provider")}
      </Button>
    );
  }

  const canSubmit = /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id.trim()) && baseUrl.trim().length > 0;

  return (
    <div className={styles.detailSection} style={{ margin: "8px", padding: "10px", display: "grid", gap: 6 }}>
      <div style={{ fontSize: 12, fontWeight: 600 }}>
        {text("Add custom provider", "添加自定义 Provider")}
      </div>
      <Input
        placeholder={text("id (e.g. frontier-intelligence)", "id（如 frontier-intelligence）")}
        value={id}
        onChange={(e) => setId(e.target.value.toLowerCase())}
        autoFocus
      />
      <Input
        placeholder={text("Display name (optional)", "显示名称（可选）")}
        value={label}
        onChange={(e) => setLabel(e.target.value)}
      />
      <Input
        placeholder={text("Base URL (e.g. https://api.example.com/v1)", "Base URL（如 https://api.example.com/v1）")}
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
      />
      {error && <div style={{ fontSize: 11, color: "var(--destructive, #e5484d)" }}>{error}</div>}
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        <Button variant="ghost" size="sm" onClick={reset} disabled={busy}>
          {text("Cancel", "取消")}
        </Button>
        <Button size="sm" onClick={submit} disabled={!canSubmit || busy}>
          {busy ? text("Adding…", "添加中…") : text("Add", "添加")}
        </Button>
      </div>
    </div>
  );
}
