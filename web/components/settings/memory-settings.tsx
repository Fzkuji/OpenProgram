"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Check, Database, PencilLine, Search, Sparkles } from "lucide-react";

import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";
import { api } from "@/lib/net/api";
import type { Model } from "@/lib/types";
import shared from "./settings-page.module.css";
import styles from "./memory-settings.module.css";

type SettingRow = {
  key: string;
  value?: unknown;
  apply: "live" | "next_start";
};

type MemoryStatus = {
  workspace_path?: string;
  embedding_available?: boolean;
};

const KEYS = [
  "memory.backend",
  "memory.writer.model",
  "memory.writer.enabled",
  "memory.writer.trigger_tokens",
  "memory.retrieval.method",
  "memory.retrieval.top_k",
  "memory.retrieval.include_sources",
  "memory.core.inject",
  "memory.recent.limit",
] as const;

export function MemorySettings() {
  const { text } = useTranslation();
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [models, setModels] = useState<Model[]>([]);
  const [defaultChat, setDefaultChat] = useState("");
  const [memoryStatus, setMemoryStatus] = useState<MemoryStatus>({});
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    Promise.all([
      fetch("/api/settings").then((response) => {
        if (!response.ok) throw new Error(`settings: ${response.status}`);
        return response.json();
      }),
      api.listEnabledModels().catch(() => []),
      api.getAgentSettings().catch(() => ({})),
      fetch("/api/memory/status").then((response) =>
        response.ok ? response.json() : {},
      ).catch(() => ({})),
    ]).then(([settingsPayload, enabledModels, agentSettings, status]) => {
      const memoryRows = (settingsPayload.settings || []).filter(
        (row: SettingRow) => KEYS.includes(row.key as typeof KEYS[number]),
      );
      setRows(memoryRows);
      setDraft(Object.fromEntries(memoryRows.map((row: SettingRow) => [row.key, row.value])));
      setModels(enabledModels);
      const chat = (agentSettings as Record<string, unknown>).chat as { provider?: string; model?: string } | undefined;
      setDefaultChat([chat?.provider, chat?.model].filter(Boolean).join("/"));
      setMemoryStatus(status);
    }).catch((error) => {
      setMessage(text(`Could not load Memory settings: ${error}`, `无法加载 Memory 设置：${error}`));
    }).finally(() => setLoaded(true));
  }, [text]);

  const changed = useMemo(() => rows.filter(
    (row) => draft[row.key] !== row.value,
  ), [draft, rows]);

  function update(key: string, value: unknown) {
    setDraft((current) => ({ ...current, [key]: value }));
    setMessage("");
  }

  async function save() {
    if (!changed.length) return;
    setSaving(true);
    setMessage("");
    try {
      for (const row of changed) {
        const response = await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: row.key, value: draft[row.key] }),
        });
        const result = await response.json();
        if (!response.ok || result.error) {
          throw new Error(result.error || `${row.key}: ${response.status}`);
        }
        setRows((current) => current.map((item) =>
          item.key === row.key ? { ...item, value: result.value } : item,
        ));
      }
      setMessage(text("Settings saved", "设置已保存"));
    } catch (error) {
      setMessage(text(`Save failed: ${error}`, `保存失败：${error}`));
    } finally {
      setSaving(false);
    }
  }

  if (!loaded) {
    return <div className={`${shared.page} ${styles.memoryPage}`}><div className={styles.loading}>{text("Loading…", "加载中…")}</div></div>;
  }

  const backend = String(draft["memory.backend"] ?? "local");
  const writerModel = String(draft["memory.writer.model"] ?? "");
  const retrieval = String(draft["memory.retrieval.method"] ?? "bm25");
  const embeddingAvailable = !!memoryStatus.embedding_available;
  const writerLabel = writerModel || defaultChat || text("Default chat model", "默认聊天模型");
  const recallLabel = retrieval === "bm25" ? "BM25" : retrieval === "embedding" ? text("Semantic", "语义") : text("Hybrid", "混合");

  return (
    <div className={`${shared.page} ${styles.memoryPage}`}>
      <div className={shared.pageHeader}>
        <h2 className={shared.pageTitle}>Memory</h2>
        <p className={shared.pageMeta}>{text(
          "Choose how OpenProgram writes, stores, and recalls long-term memory.",
          "设置 OpenProgram 如何写入、存储和检索长期记忆。",
        )}</p>
      </div>
      <div className={`${shared.pageBody} ${styles.pageBody}`}>
        <div className={styles.lifecycle} aria-label={text("Memory lifecycle", "Memory 生命周期")}>
          <LifecycleStep icon={<Database size={15} />} label={text("Capture", "采集")} value={text("Conversation archive", "对话归档")} />
          <LifecycleStep icon={<PencilLine size={15} />} label={text("Write", "写入")} value={writerLabel} />
          <LifecycleStep icon={<Sparkles size={15} />} label={text("Store", "存储")} value={backend === "local" ? text("Local + Git", "本地 + Git") : text("Disabled", "已关闭")} />
          <LifecycleStep icon={<Search size={15} />} label={text("Recall", "检索")} value={recallLabel} warn={retrieval !== "bm25" && !embeddingAvailable} />
        </div>

        <SettingsSection title={text("Memory service", "Memory 服务")}>
          <SettingsRow label={text("Enable Memory", "启用 Memory")} description={text("Turns on recall, background writing, organization, and Memory tools.", "启用检索、后台写入、整理和 Memory 工具。") }>
            <Status>{text("Next start", "下次启动")}</Status>
            <Switch checked={backend === "local"} onCheckedChange={(checked) => update("memory.backend", checked ? "local" : "none")} />
          </SettingsRow>
          <SettingsRow label={text("Storage", "存储")} description={text("Topic Markdown with derived views and Git history.", "使用 Topic Markdown、派生视图和 Git 历史。") }>
            <span className={styles.monoValue}>{backend === "local" ? text("Local workspace · Git enabled", "本地工作区 · Git 已启用") : text("Disabled", "已关闭")}</span>
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title={text("Background writing", "后台写入")}>
          <SettingsRow label={text("Writer model", "写入模型")} description={text("Uses the default chat model unless you select an enabled model.", "默认使用聊天模型，也可以选择一个已启用模型。") }>
            <select aria-label={text("Writer model", "写入模型")} className={styles.select} value={writerModel} onChange={(event) => update("memory.writer.model", event.target.value)}>
              <option value="">{text("Default chat model", "默认聊天模型")}{defaultChat ? ` · ${defaultChat}` : ""}</option>
              {models.map((model) => <option key={`${model.provider}/${model.id}`} value={`${model.provider}/${model.id}`}>{model.name} · {model.provider}</option>)}
            </select>
            <Status live>{text("Live", "实时")}</Status>
          </SettingsRow>
          <SettingsRow label={text("Automatic writing", "自动写入")} description={text("Turn completed conversations into Topic records in the background.", "在后台把已完成对话整理为 Topic 记录。") }>
            <Switch checked={Boolean(draft["memory.writer.enabled"])} onCheckedChange={(value) => update("memory.writer.enabled", value)} />
          </SettingsRow>
          <SettingsRow label={text("Write frequency", "写入频率")} description={text("Controls how much conversation accumulates before a background write.", "控制后台写入前累计的对话量。") }>
            <select aria-label={text("Write frequency", "写入频率")} className={styles.select} value={Number(draft["memory.writer.trigger_tokens"] ?? 16000)} onChange={(event) => update("memory.writer.trigger_tokens", Number(event.target.value))}>
              <option value={8000}>{text("More frequent · about 8K tokens", "更频繁 · 约 8K Token")}</option>
              <option value={16000}>{text("Balanced · about 16K tokens", "均衡 · 约 16K Token")}</option>
              <option value={32000}>{text("Less frequent · about 32K tokens", "较少 · 约 32K Token")}</option>
            </select>
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title={text("Retrieval", "检索")}>
          <SettingsRow label={text("Recall method", "检索方法")} description={text("Used before model turns and by Memory search.", "用于模型回合前的自动检索和 Memory 搜索。") }>
            <select aria-label={text("Recall method", "检索方法")} className={styles.select} value={retrieval} onChange={(event) => update("memory.retrieval.method", event.target.value)}>
              <option value="bm25">{text("Keyword · BM25", "关键词 · BM25")}</option>
              <option value="embedding" disabled={!embeddingAvailable}>{text("Semantic · Embeddings", "语义 · Embedding")}{!embeddingAvailable ? text(" (unavailable)", "（不可用）") : ""}</option>
              <option value="hybrid" disabled={!embeddingAvailable}>{text("Hybrid · BM25 + Embeddings", "混合 · BM25 + Embedding")}{!embeddingAvailable ? text(" (unavailable)", "（不可用）") : ""}</option>
            </select>
            <Status live>{text("Live", "实时")}</Status>
          </SettingsRow>
          <SettingsRow label={text("Embedding model", "Embedding 模型")} description={text("Required for semantic and hybrid retrieval.", "语义检索和混合检索需要该能力。") }>
            <Status missing={!embeddingAvailable}>{embeddingAvailable ? text("Available", "可用") : text("Not installed", "未安装")}</Status>
          </SettingsRow>
          <SettingsRow label={text("Recall depth", "检索数量")} description={text("Maximum matching records added automatically to a turn.", "每个回合自动加入的最大匹配记录数。") }>
            <select aria-label={text("Recall depth", "检索数量")} className={styles.select} value={Number(draft["memory.retrieval.top_k"] ?? 5)} onChange={(event) => update("memory.retrieval.top_k", Number(event.target.value))}>
              {[3, 5, 8, 10].map((value) => <option key={value} value={value}>{value} {text("records", "条记录")}</option>)}
            </select>
          </SettingsRow>
          <SettingsRow label={text("Search Source evidence", "检索 Source 证据")} description={text("Include archived evidence alongside curated Topic records.", "在整理后的 Topic 记录之外同时检索归档证据。") }>
            <Switch checked={Boolean(draft["memory.retrieval.include_sources"])} onCheckedChange={(value) => update("memory.retrieval.include_sources", value)} />
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title={text("Context and history", "上下文与历史")}>
          <SettingsRow label={text("Core Memory in every chat", "每次聊天注入 Core Memory")} description={text("Inject the compact Core view into each system prompt.", "把精简的 Core 视图加入每次系统提示词。") }>
            <Switch checked={Boolean(draft["memory.core.inject"])} onCheckedChange={(value) => update("memory.core.inject", value)} />
          </SettingsRow>
          <SettingsRow label={text("Recent view size", "Recent 视图大小")} description={text("Number of latest records retained after the next Memory write.", "下次 Memory 写入后保留的最新记录数量。") }>
            <select aria-label={text("Recent view size", "Recent 视图大小")} className={styles.select} value={Number(draft["memory.recent.limit"] ?? 50)} onChange={(event) => update("memory.recent.limit", Number(event.target.value))}>
              {[25, 50, 100].map((value) => <option key={value} value={value}>{value} {text("records", "条记录")}</option>)}
            </select>
          </SettingsRow>
          <SettingsRow label={text("Workspace", "工作区")} description={text("Current Memory data location. Runtime-managed files remain read-only.", "当前 Memory 数据位置；Runtime 管理的文件保持只读。") }>
            <span className={styles.monoValue}>{memoryStatus.workspace_path || "~/.openprogram/memory"}</span>
          </SettingsRow>
        </SettingsSection>
      </div>
      <div className={styles.saveBar}>
        <span className={message.startsWith("Save failed") || message.startsWith("保存失败") ? styles.error : styles.saved}>{message && <><Check size={13} />{message}</>}</span>
        <button className={styles.saveButton} type="button" onClick={save} disabled={saving || changed.length === 0}>{saving ? text("Saving…", "保存中…") : text("Save changes", "保存更改")}</button>
      </div>
    </div>
  );
}

function LifecycleStep({ icon, label, value, warn = false }: { icon: ReactNode; label: string; value: string; warn?: boolean }) {
  return <div className={styles.lifecycleStep}><span className={styles.lifecycleLabel}>{label}</span><strong className={warn ? styles.lifecycleWarn : ""}>{icon}{value}</strong></div>;
}

function SettingsSection({ title, children }: { title: string; children: ReactNode }) {
  return <section><h3 className={shared.sectionTitle}>{title}</h3><div className={styles.card}>{children}</div></section>;
}

function SettingsRow({ label, description, children }: { label: string; description: string; children: ReactNode }) {
  return <div className={styles.row}><div className={styles.rowCopy}><strong>{label}</strong><span>{description}</span></div><div className={styles.controls}>{children}</div></div>;
}

function Status({ children, live = false, missing = false }: { children: ReactNode; live?: boolean; missing?: boolean }) {
  return <span className={`${styles.status} ${live ? styles.statusLive : ""} ${missing ? styles.statusMissing : ""}`}>{children}</span>;
}
