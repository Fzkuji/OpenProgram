"use client";

import { useState } from "react";
import { Brain, Eye, Video, Wrench } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";

import { ProviderIcon } from "../provider-icon";

import styles from "../settings-page.module.css";
import { formatCtx, type Model, type Provider } from "./types";

/** Searchable + bulk-toggleable list of a provider's models, plus a
 *  "Fetch models" button when the backend can refresh from the
 *  provider's own listing endpoint. */
export function ModelList({
  provider,
  models,
  search,
  onSearch,
  onReload,
}: {
  provider: Provider;
  models: Model[];
  search: string;
  onSearch: (s: string) => void;
  onReload: () => void;
}) {
  const enabledCount = models.filter((m) => m.enabled).length;
  const filtered = !search
    ? models
    : models.filter((m) => {
        const q = search.toLowerCase();
        return (
          (m.name || "").toLowerCase().includes(q) ||
          (m.id || "").toLowerCase().includes(q)
        );
      });

  async function toggle(modelId: string, enabled: boolean) {
    try {
      await fetch(
        `/api/providers/${encodeURIComponent(provider.id)}/models/${encodeURIComponent(modelId)}/toggle`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        },
      );
    } catch { /* ignore */ }
    onReload();
  }

  async function bulkToggle(enabled: boolean) {
    const targets = models.filter((m) => m.enabled !== enabled);
    await Promise.all(
      targets.map((m) =>
        fetch(
          `/api/providers/${encodeURIComponent(provider.id)}/models/${encodeURIComponent(m.id)}/toggle`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled }),
          },
        ),
      ),
    );
    onReload();
  }

  const [fetchStatus, setFetchStatus] = useState<string | null>(null);
  async function fetchRemote() {
    setFetchStatus("Fetching…");
    try {
      const r = await fetch(
        `/api/providers/${encodeURIComponent(provider.id)}/fetch-models`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        },
      );
      const d = await r.json();
      if (d.error) {
        setFetchStatus("Failed: " + d.error);
        // Auto-clear failure message after 6 s so the row resets.
        setTimeout(() => setFetchStatus(null), 6_000);
        return;
      }
      // Brief summary; added > 0 → new rows merged; added == 0 →
      // registry already had everything the provider returned.
      const summary = d.added > 0
        ? `Fetched ${d.fetched}, added ${d.added} new`
        : `Fetched ${d.fetched} — already up to date`;
      setFetchStatus(summary);
      onReload();
      setTimeout(() => setFetchStatus(null), 4_000);
    } catch (e) {
      setFetchStatus("Failed: " + (e as Error).message);
      setTimeout(() => setFetchStatus(null), 6_000);
    }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>
          Models{" "}
          <span className={styles.modelCountSummary}>
            {enabledCount} / {models.length} available
          </span>
        </span>
        <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {fetchStatus && (
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {fetchStatus}
            </span>
          )}
          {provider.supports_fetch && (
            <Button variant="outline" size="sm" onClick={fetchRemote}>
              Fetch models
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => bulkToggle(true)}>
            Enable all
          </Button>
          <Button variant="outline" size="sm" onClick={() => bulkToggle(false)}>
            Disable all
          </Button>
        </span>
      </div>
      <div className={styles.modelSearch}>
        <input
          type="search"
          placeholder="Search models…"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
        />
      </div>
      <div className={styles.modelList}>
        {filtered.map((m) => (
          <ModelRow
            key={m.id}
            providerId={provider.id}
            model={m}
            onToggle={(en) => toggle(m.id, en)}
          />
        ))}
      </div>
    </div>
  );
}

/** Single model row — name + id + capability badges + on/off switch. */
function ModelRow({
  providerId,
  model,
  onToggle,
}: {
  providerId: string;
  model: Model;
  onToggle: (enabled: boolean) => void;
}) {
  const caps: React.ReactNode[] = [];
  if (model.vision) caps.push(<span key="v" className={styles.capBadge + " " + styles.vision} title="Vision"><Eye size={11} strokeWidth={1.8} /></span>);
  if (model.video) caps.push(<span key="vid" className={styles.capBadge + " " + styles.video} title="Video"><Video size={11} strokeWidth={1.8} /></span>);
  if (model.tools) caps.push(<span key="t" className={styles.capBadge + " " + styles.tools} title="Tools"><Wrench size={11} strokeWidth={1.8} /></span>);
  if (model.reasoning) caps.push(<span key="r" className={styles.capBadge + " " + styles.reasoning} title="Reasoning"><Brain size={11} strokeWidth={1.8} /></span>);
  if (model.context_window)
    caps.push(<span key="c" className={styles.capBadge + " " + styles.ctx}>{formatCtx(model.context_window)}</span>);

  return (
    <div className={styles.modelItem}>
      <div className={styles.modelItemIcon}>
        <ProviderIcon id={providerId} size={20} />
      </div>
      <div className={styles.modelItemInfo}>
        <span className={styles.modelItemName}>{model.name || model.id}</span>
        <span className={styles.modelItemId}>{model.id}</span>
      </div>
      <div className={styles.modelCapabilities}>{caps}</div>
      <Switch checked={model.enabled} onCheckedChange={onToggle} />
    </div>
  );
}
