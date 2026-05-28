"use client";

import { useState } from "react";
import {
  Brain,
  ChevronRight,
  Eye,
  Headphones,
  Lock,
  Paperclip,
  Sliders,
  Unlock,
  Video,
  Wrench,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";

import { ProviderIcon } from "../provider-icon";

import styles from "../settings-page.module.css";
import { formatCtx, type Model, type Provider } from "./types";
import { useTranslation } from "@/lib/i18n";


/** Compact USD/M-token formatter for the pricing rows. ``0.435`` →
 *  ``"$0.435"``, ``0.003625`` → ``"$0.0036"`` (4 sig figs cap). */
function formatPrice(n?: number): string {
  if (n == null) return "—";
  if (n === 0) return "$0";
  if (n < 0.01) return "$" + n.toFixed(4);
  if (n < 1) return "$" + n.toFixed(3);
  return "$" + n.toFixed(2);
}

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
            <Button size="sm" onClick={fetchRemote}>
              {text("Fetch models", "获取模型")}
            </Button>
          )}
          <Button size="sm" onClick={() => bulkToggle(true)}>
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

/** Single model row — name + id + capability badges + on/off switch.
 *  Click the row (anywhere outside the toggle) to expand a details
 *  panel showing every capability + modality + pricing + metadata
 *  field the catalog has for this model. The switch lives in a
 *  ``stopPropagation`` wrapper so toggling on/off doesn't also
 *  expand/collapse. */
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
  const [open, setOpen] = useState(false);

  const caps: React.ReactNode[] = [];
  if (model.vision)
    caps.push(<span key="v" className={styles.capBadge + " " + styles.vision} title={text("Vision", "视觉")}><Eye size={11} strokeWidth={1.8} /></span>);
  if (model.video)
    caps.push(<span key="vid" className={styles.capBadge + " " + styles.video} title={text("Video", "视频")}><Video size={11} strokeWidth={1.8} /></span>);
  if (model.audio)
    caps.push(<span key="a" className={styles.capBadge} title={text("Audio", "音频")}><Headphones size={11} strokeWidth={1.8} /></span>);
  if (model.tools)
    caps.push(<span key="t" className={styles.capBadge + " " + styles.tools} title={text("Tools", "工具")}><Wrench size={11} strokeWidth={1.8} /></span>);
  if (model.reasoning)
    caps.push(<span key="r" className={styles.capBadge + " " + styles.reasoning} title={text("Reasoning", "推理")}><Brain size={11} strokeWidth={1.8} /></span>);
  if (model.structured_output)
    caps.push(<span key="s" className={styles.capBadge} title={text("Structured output (JSON schema)", "结构化输出 (JSON schema)")}><Sliders size={11} strokeWidth={1.8} /></span>);
  if (model.attachment)
    caps.push(<span key="att" className={styles.capBadge} title={text("File attachments", "文件附件")}><Paperclip size={11} strokeWidth={1.8} /></span>);
  if (model.context_window)
    caps.push(<span key="c" className={styles.capBadge + " " + styles.ctx}>{formatCtx(model.context_window)}</span>);

  return (
    <div className={styles.modelItem} style={{ flexDirection: "column", alignItems: "stretch" }}>
      {/* Header row — clickable to toggle expand. The chevron
          rotates to signal state; the toggle Switch is shielded
          from the click handler. */}
      <div
        style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
        onClick={() => setOpen((o) => !o)}
      >
        <ChevronRight
          size={14}
          strokeWidth={1.8}
          style={{
            transition: "transform 120ms",
            transform: open ? "rotate(90deg)" : "rotate(0)",
            color: "var(--text-muted)",
            flexShrink: 0,
          }}
        />
        <div className={styles.modelItemIcon}>
          <ProviderIcon id={providerId} size={20} />
        </div>
        <div className={styles.modelItemInfo}>
          <span className={styles.modelItemName}>{model.name || model.id}</span>
          <span className={styles.modelItemId}>{model.id}</span>
        </div>
        <div className={styles.modelCapabilities}>{caps}</div>
        <div onClick={(e) => e.stopPropagation()}>
          <Switch checked={model.enabled} onCheckedChange={onToggle} />
        </div>
      </div>

      {/* Expanded panel — only mounted while open so closed rows have
          zero rendering cost. */}
      {open && <ModelDetailsPanel model={model} />}
    </div>
  );
}


/** All-the-fields read-only view rendered under the row when the
 *  user clicks to expand. Four sub-sections (capabilities, limits,
 *  pricing, metadata); every line is conditional on the field being
 *  present, so DeepSeek (which doesn't expose ``cache_write_cost``)
 *  just doesn't show that row instead of rendering an em-dash. */
function ModelDetailsPanel({ model }: { model: Model }) {
  const { text } = useTranslation();

  const fact = (label: string, value: React.ReactNode) => (
    <div style={{ display: "flex", gap: 8, fontSize: 12, lineHeight: 1.6 }}>
      <span style={{ minWidth: 130, color: "var(--text-muted)" }}>{label}</span>
      <span>{value}</span>
    </div>
  );

  const boolMark = (v?: boolean) =>
    v == null
      ? <span style={{ color: "var(--text-muted)" }}>—</span>
      : v
        ? <span style={{ color: "var(--accent, #4ade80)" }}>✓ {text("Yes", "是")}</span>
        : <span style={{ color: "var(--text-muted)" }}>— {text("No", "否")}</span>;

  // Section: capabilities ---------------------------------------------
  const caps = (
    <div>
      {fact(text("Vision", "视觉"), boolMark(model.vision))}
      {fact(text("Audio", "音频"), boolMark(model.audio))}
      {fact(text("Tool calls", "工具调用"), boolMark(model.tools))}
      {fact(text("Reasoning", "推理"), boolMark(model.reasoning))}
      {fact(text("Structured output", "结构化输出"), boolMark(model.structured_output))}
      {fact(text("File attachments", "文件附件"), boolMark(model.attachment))}
      {model.temperature_param != null &&
        fact(text("Temperature param", "Temperature 参数"), boolMark(model.temperature_param))}
    </div>
  );

  // Section: modalities + limits ---------------------------------------
  const limitsBlock = (
    <div>
      {model.input_modalities &&
        fact(text("Input modalities", "输入模态"), model.input_modalities.join(", "))}
      {model.output_modalities &&
        fact(text("Output modalities", "输出模态"), model.output_modalities.join(", "))}
      {model.context_window != null &&
        fact(text("Context window", "上下文窗口"),
          `${model.context_window.toLocaleString()} tokens`)}
      {model.input_limit != null &&
        fact(text("Single-call input cap", "单次输入上限"),
          `${model.input_limit.toLocaleString()} tokens`)}
      {model.max_tokens != null && model.max_tokens > 0 &&
        fact(text("Output cap", "输出上限"),
          `${model.max_tokens.toLocaleString()} tokens`)}
    </div>
  );

  // Section: pricing ---------------------------------------------------
  const hasCost =
    model.input_cost != null ||
    model.output_cost != null ||
    model.cache_read_cost != null ||
    model.cache_write_cost != null;
  const pricing = hasCost ? (
    <div>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>
        {text("USD per 1M tokens", "美元 / 1M tokens")}
      </div>
      {model.input_cost != null && fact(text("Input", "输入"), formatPrice(model.input_cost))}
      {model.output_cost != null && fact(text("Output", "输出"), formatPrice(model.output_cost))}
      {model.cache_read_cost != null &&
        fact(text("Cache hit (read)", "缓存命中 (读)"), formatPrice(model.cache_read_cost))}
      {model.cache_write_cost != null &&
        fact(text("Cache write", "缓存写入"), formatPrice(model.cache_write_cost))}
    </div>
  ) : null;

  // Section: metadata --------------------------------------------------
  const metaBlock = (
    <div>
      {model.family && fact(text("Family", "系列"), model.family)}
      {model.knowledge_cutoff && fact(text("Knowledge cutoff", "训练截止"), model.knowledge_cutoff)}
      {model.release_date && fact(text("Released", "发布日期"), model.release_date)}
      {model.last_updated && model.last_updated !== model.release_date &&
        fact(text("Updated", "更新日期"), model.last_updated)}
      {model.open_weights != null &&
        fact(
          text("Open weights", "开放权重"),
          model.open_weights
            ? <span><Unlock size={11} style={{ display: "inline", marginRight: 4 }} />{text("Yes", "是")}</span>
            : <span><Lock size={11} style={{ display: "inline", marginRight: 4 }} />{text("No", "否")}</span>,
        )}
      {model.api && fact("API", <code style={{ fontSize: 11 }}>{model.api}</code>)}
    </div>
  );

  const heading = (s: string) => (
    <div style={{
      fontSize: 11,
      fontWeight: 600,
      letterSpacing: "0.04em",
      textTransform: "uppercase",
      color: "var(--text-muted)",
      marginBottom: 4,
      marginTop: 8,
    }}>{s}</div>
  );

  return (
    <div
      style={{
        marginTop: 8,
        marginLeft: 38,                   // align with the row's name column
        padding: "8px 12px 12px",
        background: "var(--bg-secondary)",
        borderLeft: "2px solid var(--border)",
        borderRadius: "0 6px 6px 0",
      }}
    >
      {heading(text("Capabilities", "能力"))}
      {caps}
      {heading(text("Limits", "容量"))}
      {limitsBlock}
      {pricing && (
        <>
          {heading(text("Pricing", "价格"))}
          {pricing}
        </>
      )}
      {heading(text("Metadata", "元数据"))}
      {metaBlock}
    </div>
  );
}
