"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Database, PencilLine, Search, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

import { SimpleSelect } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useTranslation } from "@/lib/i18n";
import { api } from "@/lib/net/api";
import type { Model } from "@/lib/types";
import styles from "./memory-settings.module.css";
import { SettingsPage, SettingsRow, SettingsSection } from "./settings-ui";

type SettingRow = {
  key: string;
  value?: unknown;
  apply: "live" | "next_start";
};

type MemoryStatus = {
  workspace_path?: string;
  embedding_available?: boolean;
  embedding_packages?: boolean;
  failed?: boolean;
};

const KEYS = [
  "memory.backend",
  "memory.writer.model",
  "memory.writer.enabled",
  "memory.writer.trigger_tokens",
  "memory.writer.verify",
  "memory.retrieval.method",
  "memory.retrieval.top_k",
  "memory.retrieval.hybrid.weighting",
  "memory.retrieval.hybrid.file_boost",
  "memory.layers.project",
  "memory.layers.project_writes",
  "memory.core.inject",
  "memory.recent.limit",
] as const;

/** How long the page waits for the status probe and model list before showing. */
export const OPTIONAL_LOAD_WAIT_MS = 1000;

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
    // The page appears once, whole. Only the settings themselves are required:
    // a status probe or model list that never answers must not hold the form
    // back, so those two get a short wait and fill in if they answer later.
    let alive = true;
    const status = fetch("/api/memory/status?settings=true")
      .then((response) => {
        if (!response.ok) throw new Error(`memory status: ${response.status}`);
        return response.json() as Promise<MemoryStatus>;
      })
      .catch((): MemoryStatus => ({ failed: true }))
      .then((value) => { if (alive) setMemoryStatus(value); });
    const settings = fetch("/api/settings?scope=memory").then((response) => {
      if (!response.ok) throw new Error(`settings: ${response.status}`);
      return response.json();
    });
    const enabledModels = api.listEnabledModels()
      .catch((): Model[] => [])
      .then((value) => { if (alive) setModels(value); });
    let wait: ReturnType<typeof setTimeout> | undefined;
    const optional = Promise.race([
      Promise.all([status, enabledModels]),
      new Promise((resolve) => { wait = setTimeout(resolve, OPTIONAL_LOAD_WAIT_MS); }),
    ]);
    void Promise.all([settings.then(
      (payload) => ({ ok: true as const, payload }),
      (reason: unknown) => ({ ok: false as const, reason }),
    ), optional]).then(([result]) => {
      if (!alive) return;
      if (result.ok) {
        const memoryRows = (result.payload.settings || []).filter(
          (row: SettingRow) => KEYS.includes(row.key as typeof KEYS[number]),
        );
        setDraft(Object.fromEntries(memoryRows.map((row: SettingRow) => [row.key, row.value])));
        setSettingsReady(true);
      } else {
        setMessage(text(`Could not load Memory settings: ${result.reason}`, `无法加载记忆设置：${result.reason}`));
      }
      setLoaded(true);
    });
    return () => { alive = false; clearTimeout(wait); };
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
      setMemoryStatus((current) => ({ ...current, embedding_available: true, embedding_packages: true, failed: false }));
    } catch (error) {
      setMessage(text(`Embedding install failed: ${error}`, `Embedding 安装失败：${error}`));
    } finally {
      setInstalling(false);
    }
  }

  const pageTitle = t("settings.tab.memory");
  const pageDescription = text(
    "Configure how OpenProgram writes, stores, and recalls long-term memory.",
    "配置 OpenProgram 写入、存储和检索长期记忆的方式。",
  );

  if (!loaded) {
    return <SettingsPage title={pageTitle} description={pageDescription}><div className={styles.loading}>{text("Loading…", "加载中…")}</div></SettingsPage>;
  }

  const backend = String(draft["memory.backend"] ?? "local");
  const writerModel = String(draft["memory.writer.model"] ?? "");
  const retrieval = String(draft["memory.retrieval.method"] ?? "bm25");
  const embeddingAvailable = memoryStatus?.embedding_available === true;
  const writerLabel = writerModel || text("Automatic", "自动");
  const projectMemory = Boolean(draft["memory.layers.project"]);
  const recallLabel = retrieval === "agent" ? "Agent" : retrieval === "bm25" ? "BM25" : retrieval === "embedding" ? text("Semantic", "语义") : text("Hybrid", "混合");
  const controlsDisabled = !settingsReady || saving || installing;

  return (
    <SettingsPage title={pageTitle} description={pageDescription}>
        {message && <p className={styles.error} role="alert">{message}</p>}
        <div className={styles.lifecycle} aria-label={text("Memory lifecycle", "Memory 生命周期")}>
          <LifecycleStep icon={<Database size={16} />} label={text("Capture", "采集")} value={text("Conversation archive", "对话归档")} />
          <LifecycleStep icon={<PencilLine size={16} />} label={text("Write", "写入")} value={writerLabel} />
          <LifecycleStep icon={<Sparkles size={16} />} label={text("Store", "存储")} value={backend === "local" ? text("Local + Git", "本地 + Git") : text("Disabled", "已关闭")} />
          <LifecycleStep icon={<Search size={16} />} label={text("Recall", "检索")} value={recallLabel} warn={(retrieval === "embedding" || retrieval === "hybrid") && !embeddingAvailable} />
        </div>

        <SettingsSection title={text("Memory service", "Memory 服务")}>
          <SettingsRow label={text("Enable Memory", "启用 Memory")} description={text("Turns on recall, background writing, organization, and Memory tools. Changing this switch requires restarting OpenProgram.", "启用检索、后台写入、整理和 Memory 工具。更改此开关后需要重启 OpenProgram 才会生效。") }>
            <Switch aria-label={text("Enable Memory", "启用 Memory")} disabled={controlsDisabled} checked={backend === "local"} onCheckedChange={(checked) => update("memory.backend", checked ? "local" : "none")} />
          </SettingsRow>
          <SettingsRow label={text("Storage", "存储")} description={text("Topic Markdown with derived views and Git history.", "使用 Topic Markdown、派生视图和 Git 历史。") }>
            <span className={styles.chromeValue}>{backend === "local" ? text("Local workspace · Git enabled", "本地工作区 · Git 已启用") : text("Disabled", "已关闭")}</span>
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title={text("Background writing", "后台写入")}>
          <SettingsRow label={text("Writer model", "写入模型")} description={text("Automatic uses the default Agent's model, or the last used model when that Agent has none. You can also select an enabled model.", "自动：使用默认 Agent 的模型；该 Agent 没有设模型时，使用上次使用的模型。也可以选择一个已启用模型。") }>
            <Status live>{text("Live", "实时")}</Status>
            <SimpleSelect aria-label={text("Writer model", "写入模型")} disabled={controlsDisabled} value={writerModel} onValueChange={(value) => update("memory.writer.model", value)} options={[
              { value: "", label: text("Automatic", "自动") },
              // A saved model that has since been turned off stays visible.
              ...(writerModel && !models.some((model) => `${model.provider}/${model.id}` === writerModel)
                ? [{ value: writerModel, label: writerModel }] : []),
              ...models.map((model) => ({ value: `${model.provider}/${model.id}`, label: `${model.name} · ${model.provider}` })),
            ]} />
          </SettingsRow>
          <SettingsRow label={text("Automatic writing", "自动写入")} description={text("Turn completed conversations into Topic records in the background.", "在后台把已完成对话整理为 Topic 记录。") }>
            <Switch aria-label={text("Automatic writing", "自动写入")} disabled={controlsDisabled} checked={Boolean(draft["memory.writer.enabled"])} onCheckedChange={(value) => update("memory.writer.enabled", value)} />
          </SettingsRow>
          <SettingsRow label={text("Write frequency", "写入频率")} description={text("How many tokens of conversation build up before a background write.", "累计多少 Token 的对话后进行一次后台写入。") }>
            <SimpleSelect aria-label={text("Write frequency", "写入频率")} disabled={controlsDisabled} value={String(draft["memory.writer.trigger_tokens"] ?? 16000)} onValueChange={(value) => update("memory.writer.trigger_tokens", Number(value))} options={[
              { value: "8000", label: text("More often · 8K", "更频繁 · 8K") },
              { value: "16000", label: text("Balanced · 16K", "均衡 · 16K") },
              { value: "32000", label: text("Less often · 32K", "较少 · 32K") },
            ]} />
          </SettingsRow>
          <SettingsRow label={text("Verify after writing", "写入后校验")} description={text("After each write, ask a question about what was written, look it up, and repair Memory when the answer is not found. Costs a few extra model calls per write.", "每次写入后，就刚写入的内容提一个问题并检索；找不到答案时修复 Memory。每次写入会多几次模型调用。") }>
            <Switch aria-label={text("Verify after writing", "写入后校验")} disabled={controlsDisabled} checked={Boolean(draft["memory.writer.verify"])} onCheckedChange={(value) => update("memory.writer.verify", value)} />
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title={text("Retrieval", "检索")}>
          <SettingsRow label={text("Recall method", "检索方法")} description={text("Use ranked automatic recall, or let the Agent inspect Memory only when needed.", "使用自动排序检索，或仅由 Agent 在需要时查看 Memory。") }>
            <Status live>{text("Live", "实时")}</Status>
            <SimpleSelect aria-label={text("Recall method", "检索方法")} disabled={controlsDisabled} value={retrieval} onValueChange={(value) => update("memory.retrieval.method", value)} options={[
              { value: "agent", label: text("Agent · On demand", "Agent · 按需查看") },
              { value: "bm25", label: text("Keyword · BM25", "关键词 · BM25") },
              { value: "embedding", disabled: !embeddingAvailable, label: text("Semantic · Embeddings", "语义 · Embedding") + (!embeddingAvailable ? text(" (unavailable)", "（不可用）") : "") },
              { value: "hybrid", disabled: !embeddingAvailable, label: text("Hybrid", "混合") + (!embeddingAvailable ? text(" (unavailable)", "（不可用）") : "") },
            ]} />
          </SettingsRow>
          <SettingsRow label={text("Embedding model", "Embedding 模型")} description={text("Required only for semantic and hybrid retrieval. Installing also adds sentence-transformers and PyTorch when OpenProgram does not include them.", "仅语义检索和混合检索需要该模型。OpenProgram 未自带 sentence-transformers 和 PyTorch 时，安装会一并装上。") }>
            <Status missing={memoryStatus !== null && !memoryStatus.failed && !embeddingAvailable}>{memoryStatus === null ? text("Checking…", "检查中…") : memoryStatus.failed ? text("Unavailable", "无法检查") : embeddingAvailable ? text("Available", "可用") : text("Not installed", "未安装")}</Status>
            {memoryStatus !== null && !memoryStatus.failed && !embeddingAvailable && <Button variant="elevated" type="button" onClick={installEmbedding} disabled={saving || installing}>{installing ? text("Installing…", "安装中…") : memoryStatus.embedding_packages === false ? text("Install · about 1 GB", "安装 · 约 1 GB") : text("Install · about 90 MB", "安装 · 约 90 MB")}</Button>}
          </SettingsRow>
          <SettingsRow label={text("Hybrid weighting", "混合权重")} description={text("How hybrid recall balances keyword and semantic matches. Automatic leans on keywords for dates, quoted phrases and names.", "混合检索中关键词与语义匹配的比重。自动：查询含日期、引号短语或人名时偏向关键词。") }>
            <SimpleSelect aria-label={text("Hybrid weighting", "混合权重")} disabled={controlsDisabled || retrieval !== "hybrid"} value={String(draft["memory.retrieval.hybrid.weighting"] ?? "auto")} onValueChange={(value) => update("memory.retrieval.hybrid.weighting", value)} options={[
              { value: "auto", label: text("Automatic", "自动") },
              { value: "keyword", label: text("Keyword first · 70 / 30", "关键词优先 · 70 / 30") },
              { value: "balanced", label: text("Balanced · 50 / 50", "均衡 · 50 / 50") },
              { value: "semantic", label: text("Semantic first · 30 / 70", "语义优先 · 30 / 70") },
            ]} />
          </SettingsRow>
          <SettingsRow label={text("Favour well-matched Topics", "同主题加权")} description={text("In hybrid recall, raise a Topic file that several matches come from.", "混合检索时，多条匹配来自同一个 Topic 文件时提高它的排名。") }>
            <Switch aria-label={text("Favour well-matched Topics", "同主题加权")} disabled={controlsDisabled || retrieval !== "hybrid"} checked={Boolean(draft["memory.retrieval.hybrid.file_boost"])} onCheckedChange={(value) => update("memory.retrieval.hybrid.file_boost", value)} />
          </SettingsRow>
          <SettingsRow label={text("Recall depth", "检索数量")} description={text("Maximum matching records added automatically to a turn.", "每个回合自动加入的最大匹配记录数。") }>
            <SimpleSelect aria-label={text("Recall depth", "检索数量")} disabled={controlsDisabled || retrieval === "agent"} value={String(draft["memory.retrieval.top_k"] ?? 5)} onValueChange={(value) => update("memory.retrieval.top_k", Number(value))} options={[3, 5, 8, 10].map((value) => ({ value: String(value), label: `${value} ${text("records", "条记录")}` }))} />
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title={text("Project memory", "项目记忆")}>
          <SettingsRow label={text("Project memory", "项目记忆")} description={text("A conversation in a project also reads that project's own Memory, kept in <project>/.openprogram/memory.", "项目中的对话同时读取该项目自己的 Memory，存放在 <项目>/.openprogram/memory。") }>
            <Switch aria-label={text("Project memory", "项目记忆")} disabled={controlsDisabled} checked={projectMemory} onCheckedChange={(value) => update("memory.layers.project", value)} />
          </SettingsRow>
          <SettingsRow label={text("Write to project memory", "写入项目记忆")} description={text("Write what a project's conversations record into that project's Memory instead of the personal one.", "把项目对话要记下的内容写入该项目的 Memory，而不是个人 Memory。") }>
            <Switch aria-label={text("Write to project memory", "写入项目记忆")} disabled={controlsDisabled || !projectMemory} checked={Boolean(draft["memory.layers.project_writes"])} onCheckedChange={(value) => update("memory.layers.project_writes", value)} />
          </SettingsRow>
        </SettingsSection>

        <SettingsSection title={text("Context and history", "上下文与历史")}>
          <SettingsRow label={text("Core Memory in every chat", "每次聊天注入核心记忆")} description={text("Inject the compact Core view into each system prompt.", "把精简的核心视图加入每次系统提示词。") }>
            <Switch aria-label={text("Core Memory in every chat", "每次聊天注入核心记忆")} disabled={controlsDisabled} checked={Boolean(draft["memory.core.inject"])} onCheckedChange={(value) => update("memory.core.inject", value)} />
          </SettingsRow>
          <SettingsRow label={text("Recent view size", "Recent 视图大小")} description={text("Number of latest records retained after the next Memory write.", "下次 Memory 写入后保留的最新记录数量。") }>
            <SimpleSelect aria-label={text("Recent view size", "Recent 视图大小")} disabled={controlsDisabled} value={String(draft["memory.recent.limit"] ?? 50)} onValueChange={(value) => update("memory.recent.limit", Number(value))} options={[25, 50, 100].map((value) => ({ value: String(value), label: `${value} ${text("records", "条记录")}` }))} />
          </SettingsRow>
          <SettingsRow label={text("Workspace", "工作区")} description={text("Current Memory data location. Runtime-managed files remain read-only.", "当前 Memory 数据位置；Runtime 管理的文件保持只读。") }>
            <span className={styles.pathValue}>{memoryStatus?.workspace_path || "~/.openprogram/memory"}</span>
          </SettingsRow>
        </SettingsSection>
    </SettingsPage>
  );
}

function LifecycleStep({ icon, label, value, warn = false }: { icon: ReactNode; label: string; value: string; warn?: boolean }) {
  return <div className={styles.lifecycleStep}><span className={styles.lifecycleLabel}>{label}</span><strong className={warn ? styles.lifecycleWarn : ""}>{icon}{value}</strong></div>;
}

function Status({ children, live = false, missing = false }: { children: ReactNode; live?: boolean; missing?: boolean }) {
  return <span className={`${styles.status} ${live ? styles.statusLive : ""} ${missing ? styles.statusMissing : ""}`}>{children}</span>;
}
