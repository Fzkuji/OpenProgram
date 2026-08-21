"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Database, PencilLine, Search, Sparkles } from "lucide-react";

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
  failed?: boolean;
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
  const { t, text } = useTranslation();
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [models, setModels] = useState<Model[]>([]);
  const [memoryStatus, setMemoryStatus] = useState<MemoryStatus | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [settingsReady, setSettingsReady] = useState(false);
  const [saving, setSaving] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    void fetch("/api/memory/status?settings=true").then((response) => {
      if (!response.ok) throw new Error(`memory status: ${response.status}`);
      return response.json();
    }).then(setMemoryStatus).catch(() => setMemoryStatus({ failed: true }));

    void fetch("/api/settings?scope=memory").then((response) => {
      if (!response.ok) throw new Error(`settings: ${response.status}`);
      return response.json();
    }).then((settingsPayload) => {
      const memoryRows = (settingsPayload.settings || []).filter(
        (row: SettingRow) => KEYS.includes(row.key as typeof KEYS[number]),
      );
      setDraft(Object.fromEntries(memoryRows.map((row: SettingRow) => [row.key, row.value])));
      setSettingsReady(true);
    }).catch((error) => {
      setMessage(text(`Could not load Memory settings: ${error}`, `无法加载 Memory 设置：${error}`));
    }).finally(() => setLoaded(true));

    void api.listEnabledModels().then(setModels).catch(() => setModels([]));
  }, [text]);

  async function update(key: string, value: unknown) {
    if (!settingsReady || saving || installing) return;
    const previous = draft[key];
    setDraft((current) => ({ ...current, [key]: value }));
    setSaving(true);
    setMessage("");
    try {
      const response = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, value }),
      });
      const result = await response.json();
      if (!response.ok || result.error) {
        throw new Error(result.error || `${key}: ${response.status}`);
      }
      if (result.value !== undefined) {
        setDraft((current) => ({ ...current, [key]: result.value }));
      }
    } catch (error) {
      setDraft((current) => ({ ...current, [key]: previous }));
      setMessage(text(`Save failed: ${error}`, `保存失败：${error}`));
    } finally {
      setSaving(false);
    }
  }

  async function installEmbedding() {
    setInstalling(true);
    setMessage("");
    try {
      const response = await fetch("/api/memory/embedding/install", { method: "POST" });
      const result = await response.json();
      if (!response.ok || !result.embedding_available) {
        throw new Error(result.error || `embedding install: ${response.status}`);
      }
      setMemoryStatus((current) => ({ ...current, embedding_available: true, failed: false }));
    } catch (error) {
      setMessage(text(`Embedding install failed: ${error}`, `Embedding 安装失败：${error}`));
    } finally {
      setInstalling(false);
    }
  }

  const pageHeader = (
    <div className={shared.pageHeader}>
      <h2 className={shared.pageTitle}>{t("settings.tab.memory")}</h2>
      <p className={shared.pageMeta}>{text(
        "Configure how OpenProgram writes, stores, and recalls long-term memory.",
        "配置 OpenProgram 写入、存储和检索长期记忆的方式。",
      )}</p>
    </div>
  );

  if (!loaded) {
    return <div className={shared.page}>{pageHeader}<div className={styles.loading}>{text("Loading…", "加载中…")}</div></div>;
  }

  const backend = String(draft["memory.backend"] ?? "local");
  const writerModel = String(draft["memory.writer.model"] ?? "");
  const retrieval = String(draft["memory.retrieval.method"] ?? "bm25");
  const embeddingAvailable = memoryStatus?.embedding_available === true;
  const writerLabel = writerModel || text("Default chat model", "默认聊天模型");
  const recallLabel = retrieval === "agent" ? "Agent" : retrieval === "bm25" ? "BM25" : retrieval === "embedding" ? text("Semantic", "语义") : text("Hybrid", "混合");
  const controlsDisabled = !settingsReady || saving || installing;

  return (
    <div className={shared.page}>
      {pageHeader}
      <div className={shared.pageBody}>
        {message && <p className={styles.error} role="alert">{message}</p>}
        <div className={styles.lifecycle} aria-label={text("Memory lifecycle", "Memory 生命周期")}>
          <LifecycleStep icon={<Database size={15} />} label={text("Capture", "采集")} value={text("Conversation archive", "对话归档")} />
          <LifecycleStep icon={<PencilLine size={15} />} label={text("Write", "写入")} value={writerLabel} />
          <LifecycleStep icon={<Sparkles size={15} />} label={text("Store", "存储")} value={backend === "local" ? text("Local + Git", "本地 + Git") : text("Disabled", "已关闭")} />
          <LifecycleStep icon={<Search size={15} />} label={text("Recall", "检索")} value={recallLabel} warn={(retrieval === "embedding" || retrieval === "hybrid") && !embeddingAvailable} />
        </div>

        <SettingsSection title={text("Memory service", "Memory 服务")}>
          <SettingsRow label={text("Enable Memory", "启用 Memory")} description={text("Turns on recall, background writing, organization, and Memory tools.", "启用检索、后台写入、整理和 Memory 工具。") }>
            <Status>{text("Next start", "下次启动")}</Status>
            <Switch aria-label={text("Enable Memory", "启用 Memory")} disabled={controlsDisabled} checked={backend === "local"} onCheckedChange={(checked) => update("memory.backend", checked ? "local" : "none")} />
          </SettingsRow>
          <SettingsRow label={text("Storage", "存储")} description={text("Topic Markdown with derived views and Git history.", "使用 Topic Markdown、派生视图和 Git 历史。") }>
            <span className={styles.chromeValue}>{backend === "local" ? text("Local workspace · Git enabled", "本地工作区 · Git 已启用") : text("Disabled", "已关闭")}</span>
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title={text("Background writing", "后台写入")}>
          <SettingsRow label={text("Writer model", "写入模型")} description={text("Uses the default chat model unless you select an enabled model.", "默认使用聊天模型，也可以选择一个已启用模型。") }>
            <Status live>{text("Live", "实时")}</Status>
            <select aria-label={text("Writer model", "写入模型")} disabled={controlsDisabled} className={styles.select} value={writerModel} onChange={(event) => update("memory.writer.model", event.target.value)}>
              <option value="">{text("Default chat model", "默认聊天模型")}</option>
              {models.map((model) => <option key={`${model.provider}/${model.id}`} value={`${model.provider}/${model.id}`}>{model.name} · {model.provider}</option>)}
            </select>
          </SettingsRow>
          <SettingsRow label={text("Automatic writing", "自动写入")} description={text("Turn completed conversations into Topic records in the background.", "在后台把已完成对话整理为 Topic 记录。") }>
            <Switch aria-label={text("Automatic writing", "自动写入")} disabled={controlsDisabled} checked={Boolean(draft["memory.writer.enabled"])} onCheckedChange={(value) => update("memory.writer.enabled", value)} />
          </SettingsRow>
          <SettingsRow label={text("Write frequency", "写入频率")} description={text("Controls how much conversation accumulates before a background write.", "控制后台写入前累计的对话量。") }>
            <select aria-label={text("Write frequency", "写入频率")} disabled={controlsDisabled} className={styles.select} value={Number(draft["memory.writer.trigger_tokens"] ?? 16000)} onChange={(event) => update("memory.writer.trigger_tokens", Number(event.target.value))}>
              <option value={8000}>{text("More frequent · about 8K tokens", "更频繁 · 约 8K Token")}</option>
              <option value={16000}>{text("Balanced · about 16K tokens", "均衡 · 约 16K Token")}</option>
              <option value={32000}>{text("Less frequent · about 32K tokens", "较少 · 约 32K Token")}</option>
            </select>
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title={text("Retrieval", "检索")}>
          <SettingsRow label={text("Recall method", "检索方法")} description={text("Use ranked automatic recall, or let the Agent inspect Memory only when needed.", "使用自动排序检索，或仅由 Agent 在需要时查看 Memory。") }>
            <Status live>{text("Live", "实时")}</Status>
            <select aria-label={text("Recall method", "检索方法")} disabled={controlsDisabled} className={styles.select} value={retrieval} onChange={(event) => update("memory.retrieval.method", event.target.value)}>
              <option value="agent">{text("Agent · On demand", "Agent · 按需查看")}</option>
              <option value="bm25">{text("Keyword · BM25", "关键词 · BM25")}</option>
              <option value="embedding" disabled={!embeddingAvailable}>{text("Semantic · Embeddings", "语义 · Embedding")}{!embeddingAvailable ? text(" (unavailable)", "（不可用）") : ""}</option>
              <option value="hybrid" disabled={!embeddingAvailable}>{text("Hybrid · BM25 + Embeddings", "混合 · BM25 + Embedding")}{!embeddingAvailable ? text(" (unavailable)", "（不可用）") : ""}</option>
            </select>
          </SettingsRow>
          <SettingsRow label={text("Embedding model", "Embedding 模型")} description={text("Required only for semantic and hybrid retrieval.", "仅语义检索和混合检索需要该模型。") }>
            <Status missing={memoryStatus !== null && !memoryStatus.failed && !embeddingAvailable}>{memoryStatus === null ? text("Checking…", "检查中…") : memoryStatus.failed ? text("Unavailable", "无法检查") : embeddingAvailable ? text("Available", "可用") : text("Not installed", "未安装")}</Status>
            {memoryStatus !== null && !memoryStatus.failed && !embeddingAvailable && <button className={styles.installButton} type="button" onClick={installEmbedding} disabled={saving || installing}>{installing ? text("Installing…", "安装中…") : text("Install · about 90 MB", "安装 · 约 90 MB")}</button>}
          </SettingsRow>
          <SettingsRow label={text("Recall depth", "检索数量")} description={text("Maximum matching records added automatically to a turn.", "每个回合自动加入的最大匹配记录数。") }>
            <select aria-label={text("Recall depth", "检索数量")} disabled={controlsDisabled || retrieval === "agent"} className={styles.select} value={Number(draft["memory.retrieval.top_k"] ?? 5)} onChange={(event) => update("memory.retrieval.top_k", Number(event.target.value))}>
              {[3, 5, 8, 10].map((value) => <option key={value} value={value}>{value} {text("records", "条记录")}</option>)}
            </select>
          </SettingsRow>
          <SettingsRow label={text("Search Source evidence", "检索 Source 证据")} description={text("Include archived evidence alongside curated Topic records.", "在整理后的 Topic 记录之外同时检索归档证据。") }>
            <Switch aria-label={text("Search Source evidence", "检索 Source 证据")} disabled={controlsDisabled || retrieval === "agent"} checked={Boolean(draft["memory.retrieval.include_sources"])} onCheckedChange={(value) => update("memory.retrieval.include_sources", value)} />
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title={text("Context and history", "上下文与历史")}>
          <SettingsRow label={text("Core Memory in every chat", "每次聊天注入 Core Memory")} description={text("Inject the compact Core view into each system prompt.", "把精简的 Core 视图加入每次系统提示词。") }>
            <Switch aria-label={text("Core Memory in every chat", "每次聊天注入 Core Memory")} disabled={controlsDisabled} checked={Boolean(draft["memory.core.inject"])} onCheckedChange={(value) => update("memory.core.inject", value)} />
          </SettingsRow>
          <SettingsRow label={text("Recent view size", "Recent 视图大小")} description={text("Number of latest records retained after the next Memory write.", "下次 Memory 写入后保留的最新记录数量。") }>
            <select aria-label={text("Recent view size", "Recent 视图大小")} disabled={controlsDisabled} className={styles.select} value={Number(draft["memory.recent.limit"] ?? 50)} onChange={(event) => update("memory.recent.limit", Number(event.target.value))}>
              {[25, 50, 100].map((value) => <option key={value} value={value}>{value} {text("records", "条记录")}</option>)}
            </select>
          </SettingsRow>
          <SettingsRow label={text("Workspace", "工作区")} description={text("Current Memory data location. Runtime-managed files remain read-only.", "当前 Memory 数据位置；Runtime 管理的文件保持只读。") }>
            <span className={styles.monoValue}>{memoryStatus?.workspace_path || "~/.openprogram/memory"}</span>
          </SettingsRow>
        </SettingsSection>
      </div>
    </div>
  );
}

function LifecycleStep({ icon, label, value, warn = false }: { icon: ReactNode; label: string; value: string; warn?: boolean }) {
  return <div className={styles.lifecycleStep}><span className={styles.lifecycleLabel}>{label}</span><strong className={warn ? styles.lifecycleWarn : ""}>{icon}{value}</strong></div>;
}

function SettingsSection({ title, children }: { title: string; children: ReactNode }) {
  return <section><h3 className={shared.sectionTitle}>{title}</h3><div className={shared.card}>{children}</div></section>;
}

function SettingsRow({ label, description, children }: { label: string; description: string; children: ReactNode }) {
  return <div className={`${shared.row} ${shared.rowTop} ${shared.systemRow} ${styles.settingsRow}`}><div className={shared.label}><div>{label}</div><div className={styles.rowDescription}>{description}</div></div><div className={styles.controls}>{children}</div></div>;
}

function Status({ children, live = false, missing = false }: { children: ReactNode; live?: boolean; missing?: boolean }) {
  return <span className={`${styles.status} ${live ? styles.statusLive : ""} ${missing ? styles.statusMissing : ""}`}>{children}</span>;
}
