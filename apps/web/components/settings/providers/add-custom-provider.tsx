"use client";

import { jsonFetch } from "@/lib/net/fetch-client";
import { useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import styles from "./provider-pane.module.css";
import { useTranslation } from "@/lib/i18n";
import { Checkbox } from "@/components/ui/checkbox";

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
export function AddCustomProvider({ onCreated, defaultOpen = false, onCancel }: {
  onCreated: (id: string) => void;
  /** Start with the form open (Add provider's Custom tile). */
  defaultOpen?: boolean;
  /** Cancel leaves the form this way instead of folding it. */
  onCancel?: () => void;
}) {
  const { text } = useTranslation();
  const [open, setOpen] = useState(defaultOpen);
  const [label, setLabel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [local, setLocal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function reset() {
    setLabel("");
    setBaseUrl("");
    setLocal(false);
    setError(null);
    setOpen(false);
  }

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      const d = await jsonFetch<{ ok?: boolean; error?: string; id?: string }>("/api/providers/custom", {
        method: "POST",
        body: JSON.stringify({ label: label.trim(), base_url: baseUrl.trim(), local }),
      });
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
        variant="elevated"
        className={styles.addCustomTrigger}
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
    <div className={styles.addCustomForm}>
      <div style={{ fontSize: "var(--fs-xs)", fontWeight: 600 }}>
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
            fontSize: "var(--fs-2xs)",
            color: "var(--muted-foreground)",
            marginTop: -2,
            // A pasted API key has no break points — without this the
            // one-word "id" line stretches the grid and the whole sidebar.
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {text("id: ", "id：")}{derivedId}
        </div>
      )}
      <Input
        placeholder={text("Base URL (e.g. https://api.example.com)", "Base URL（如 https://api.example.com）")}
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
      />
      <label className="flex items-center gap-2 text-fs-xs"><Checkbox checked={local} onCheckedChange={(checked) => setLocal(checked === true)} />{text("Local server (API key optional)", "本地服务（API key 可选）")}</label>
      {error && <div style={{ fontSize: "var(--fs-2xs)", color: "var(--destructive)" }}>{error}</div>}
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        <Button variant="ghost" onClick={() => { reset(); onCancel?.(); }} disabled={busy}>
          {text("Cancel", "取消")}
        </Button>
        <Button onClick={submit} disabled={!canSubmit || busy}>
          {busy ? text("Adding…", "添加中…") : text("Add", "添加")}
        </Button>
      </div>
    </div>
  );
}
