"use client";

import { useState } from "react";
import { Brain, Eye, Video, Wrench } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";

import { ProviderIcon } from "../provider-icon";

import styles from "../settings-page.module.css";
import { formatCtx, type Model, type Provider } from "./types";
import { useTranslation } from "@/lib/i18n";

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
  const { text } = useTranslation();
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
    setFetchStatus(text("Fetching...", "获取中..."));
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
        setFetchStatus(text("Failed: ", "失败：") + d.error);
        // Auto-clear failure message after 6 s so the row resets.
        setTimeout(() => setFetchStatus(null), 6_000);
        return;
      }
      // Brief summary; added > 0 → new rows merged; added == 0 →
      // registry already had everything the provider returned.
      const summary = d.added > 0
        ? text(`Fetched ${d.fetched}, added ${d.added} new`, `已获取 ${d.fetched} 个，新增 ${d.added} 个`)
        : text(`Fetched ${d.fetched} - already up to date`, `已获取 ${d.fetched} 个，已是最新`);
      setFetchStatus(summary);
      onReload();
      setTimeout(() => setFetchStatus(null), 4_000);
    } catch (e) {
      setFetchStatus(text("Failed: ", "失败：") + (e as Error).message);
      setTimeout(() => setFetchStatus(null), 6_000);
    }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>
          {text("Models", "模型")}{" "}
          <span className={styles.modelCountSummary}>
            {text(`${enabledCount} / ${models.length} available`, `${enabledCount} / ${models.length} 可用`)}
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
              {text("Fetch models", "获取模型")}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => bulkToggle(true)}>
            {text("Enable all", "全部启用")}
          </Button>
          <Button variant="outline" size="sm" onClick={() => bulkToggle(false)}>
            {text("Disable all", "全部禁用")}
          </Button>
        </span>
      </div>
      <div className={styles.modelSearch}>
        <input
          type="search"
          placeholder={text("Search models...", "搜索模型...")}
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
  const { text } = useTranslation();
  const caps: React.ReactNode[] = [];
  if (model.vision) caps.push(<span key="v" className={styles.capBadge + " " + styles.vision} title={text("Vision", "视觉")}><Eye size={11} strokeWidth={1.8} /></span>);
  if (model.video) caps.push(<span key="vid" className={styles.capBadge + " " + styles.video} title={text("Video", "视频")}><Video size={11} strokeWidth={1.8} /></span>);
  if (model.tools) caps.push(<span key="t" className={styles.capBadge + " " + styles.tools} title={text("Tools", "工具")}><Wrench size={11} strokeWidth={1.8} /></span>);
  if (model.reasoning) caps.push(<span key="r" className={styles.capBadge + " " + styles.reasoning} title={text("Reasoning", "推理")}><Brain size={11} strokeWidth={1.8} /></span>);
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
