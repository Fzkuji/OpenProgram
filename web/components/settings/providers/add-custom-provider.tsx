"use client";

import { useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import styles from "../settings-page.module.css";
import { useTranslation } from "@/lib/i18n";

/** Preview of the id the backend derives from a name. Mirrors the server's
 *  _slugify — for display only; the response's id is authoritative. */
function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[\s_]+/g, "-")
    .replace(/[^a-z0-9-]/g, "")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** "Add custom provider" — a small inline form under the sidebar search.
 *  Creates a config-only (tier-3) OpenAI-compatible provider via
 *  POST /api/providers/custom. The user gives a Name and Base URL; the id is
 *  derived server-side. On success calls onCreated(id) with the id from the
 *  response so the parent reloads the list and selects the new provider. */
export function AddCustomProvider({ onCreated }: { onCreated: (id: string) => void }) {
  const { text } = useTranslation();
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
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
        body: JSON.stringify({ label: label.trim(), base_url: baseUrl.trim() }),
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

  const derivedId = slugify(label);
  const canSubmit = label.trim().length > 0 && baseUrl.trim().length > 0;

  return (
    <div className={styles.detailSection} style={{ margin: "8px", padding: "10px", display: "grid", gap: 6, minWidth: 0 }}>
      <div style={{ fontSize: 12, fontWeight: 600 }}>
        {text("Add custom provider", "添加自定义 Provider")}
      </div>
      <Input
        placeholder={text("Name (e.g. Frontier Intelligence)", "名称（如 Frontier Intelligence）")}
        value={label}
        onChange={(e) => setLabel(e.target.value)}
        autoFocus
      />
      {derivedId && (
        <div
          style={{
            fontSize: 11,
            color: "var(--muted-foreground, #888)",
            marginTop: -2,
            // A pasted API key has no break points — without this the
            // one-word "id" line stretches the grid and the whole sidebar.
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={derivedId}
        >
          {text("id: ", "id：")}{derivedId}
        </div>
      )}
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
