"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Check,
  Copy,
  FileText,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
} from "lucide-react";

import { BotIcon } from "@/components/animated-icons";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  ManagePageHeader,
  ManageRow,
  managePageStyles,
} from "@/components/ui/manage-page";
import { useTranslation } from "@/lib/i18n";

import settingsStyles from "@/components/settings/settings-page.module.css";
import styles from "./agents-page.module.css";

type ToolPolicy = {
  mode: "automatic" | "selected" | "none";
  preset?: string;
  allowed?: string[];
  disabled?: string[];
};
type GatePolicy = { allowed: string[]; disabled: string[]; categories?: string[] };
type McpPolicy = GatePolicy & { required: string[] };
type Agent = {
  id: string;
  name: string;
  default: boolean;
  model: { provider: string; id: string };
  thinking_effort: string;
  system_prompt: string;
  skills: GatePolicy;
  tools: ToolPolicy;
  mcp: McpPolicy;
  identity: { name: string; mention_patterns: string[] };
  session_scope: string;
  session_idle_minutes: number;
  session_daily_reset: string;
  created_at: number;
  updated_at: number;
};
type Program = { name: string; description?: string; category?: string };
type Tool = { name: string; description?: string; disabled?: boolean };
type Skill = { name: string; description?: string; category?: string; enabled?: boolean };
type McpServer = { name: string; status?: string; connected?: boolean; enabled?: boolean };
type CatalogItem = {
  name: string;
  description: string;
  group: string;
  available: boolean;
};
type WorkspaceFile = { name: string; path: string; exists: boolean };
type Workspace = { path: string; files: WorkspaceFile[] };
type TabId = "overview" | "model" | "programs" | "skills" | "mcp" | "sessions";
type PickerKind = "programs" | "skills" | "mcp";

const TABS: Array<{ id: TabId; en: string; zh: string }> = [
  { id: "overview", en: "Overview", zh: "概览" },
  { id: "model", en: "Model & Instructions", zh: "模型与指令" },
  { id: "programs", en: "Programs", zh: "Programs" },
  { id: "skills", en: "Skills", zh: "Skills" },
  { id: "mcp", en: "MCP", zh: "MCP" },
  { id: "sessions", en: "Sessions", zh: "会话" },
];

function copyAgent(agent: Agent): Agent {
  return JSON.parse(JSON.stringify(agent)) as Agent;
}

function csv(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

function policyMode(policy: GatePolicy): "all" | "selected" | "none" {
  if (policy.disabled?.includes("*")) return "none";
  if (policy.allowed?.length || policy.categories?.length) return "selected";
  return "all";
}

function selectedProgramNames(policy: ToolPolicy, presets: Record<string, string[]>): string[] {
  if (policy.mode !== "selected") return [];
  return policy.preset ? presets[policy.preset] || [] : policy.allowed || [];
}

function mutableAgent(agent: Agent) {
  return {
    updated_at: agent.updated_at,
    name: agent.name,
    model: agent.model,
    thinking_effort: agent.thinking_effort,
    system_prompt: agent.system_prompt,
    skills: agent.skills,
    tools: agent.tools,
    mcp: agent.mcp,
    identity: agent.identity,
    session_scope: agent.session_scope,
    session_idle_minutes: agent.session_idle_minutes,
    session_daily_reset: agent.session_daily_reset,
  };
}

async function responseError(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    return payload.error || payload.detail || `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status}`;
  }
}

export function AgentsPage() {
  const { text } = useTranslation();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [baseline, setBaseline] = useState<Agent | null>(null);
  const [draft, setDraft] = useState<Agent | null>(null);
  const [tab, setTab] = useState<TabId>("overview");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; message: string } | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [duplicateOpen, setDuplicateOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newId, setNewId] = useState("");
  const [deleteId, setDeleteId] = useState("");
  const [picker, setPicker] = useState<PickerKind | null>(null);
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const [catalogSearch, setCatalogSearch] = useState("");
  const [presets, setPresets] = useState<Record<string, string[]>>({});
  const [workspace, setWorkspace] = useState<Workspace | null>(null);

  const dirty = Boolean(draft && baseline && JSON.stringify(draft) !== JSON.stringify(baseline));

  const applyAgent = useCallback((agent: Agent) => {
    const clean = copyAgent(agent);
    setSelectedId(agent.id);
    setBaseline(clean);
    setDraft(copyAgent(clean));
    setNotice(null);
    setWorkspace(null);
  }, []);

  const loadAgents = useCallback(async (preferredId?: string) => {
    const response = await fetch("/api/agents");
    if (!response.ok) throw new Error(await responseError(response));
    const payload = await response.json();
    const rows = Array.isArray(payload.agents) ? payload.agents as Agent[] : [];
    setAgents(rows);
    const next = rows.find((agent) => agent.id === preferredId)
      || rows.find((agent) => agent.default)
      || rows[0];
    if (next) applyAgent(next);
    else {
      setSelectedId("");
      setBaseline(null);
      setDraft(null);
    }
  }, [applyAgent]);

  useEffect(() => {
    void loadAgents().catch((error) => {
      setNotice({ tone: "error", message: String(error) });
    }).finally(() => setLoading(false));
  }, [loadAgents]);

  useEffect(() => {
    if (tab !== "sessions" || !draft || workspace) return;
    const controller = new AbortController();
    void fetch(`/api/agents/${encodeURIComponent(draft.id)}/workspace`, {
      signal: controller.signal,
    }).then(async (response) => {
      if (!response.ok) throw new Error(await responseError(response));
      setWorkspace(await response.json());
    }).catch((error) => {
      if ((error as Error).name !== "AbortError") {
        setNotice({ tone: "error", message: String(error) });
      }
    });
    return () => controller.abort();
  }, [draft, tab, workspace]);

  function updateDraft(patch: Partial<Agent>) {
    setDraft((current) => current ? { ...current, ...patch } : current);
    setNotice(null);
  }

  function chooseAgent(agent: Agent) {
    if (dirty && !window.confirm(text("Discard unsaved changes?", "放弃未保存的修改？"))) return;
    applyAgent(agent);
    setTab("overview");
  }

  async function save() {
    if (!draft) return;
    setBusy(true);
    setNotice(null);
    try {
      const response = await fetch(`/api/agents/${encodeURIComponent(draft.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(mutableAgent(draft)),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const payload = await response.json();
      const saved = payload.agent as Agent;
      setAgents((rows) => rows.map((agent) => agent.id === saved.id ? saved : agent));
      setBaseline(copyAgent(saved));
      setDraft(copyAgent(saved));
      setNotice({ tone: "ok", message: text("Saved", "已保存") });
    } catch (error) {
      setNotice({ tone: "error", message: String(error) });
    } finally {
      setBusy(false);
    }
  }

  async function createOrDuplicate(duplicate: boolean) {
    if (!newId.trim()) return;
    setBusy(true);
    setNotice(null);
    try {
      const url = duplicate && draft
        ? `/api/agents/${encodeURIComponent(draft.id)}/duplicate`
        : "/api/agents";
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: newId.trim(), name: newName.trim() }),
      });
      if (!response.ok) throw new Error(await responseError(response));
      const payload = await response.json();
      setAgents((rows) => [...rows, payload.agent]);
      applyAgent(payload.agent);
      setTab("overview");
      setCreateOpen(false);
      setDuplicateOpen(false);
      setNewName("");
      setNewId("");
    } catch (error) {
      setNotice({ tone: "error", message: String(error) });
    } finally {
      setBusy(false);
    }
  }

  function openDuplicate() {
    if (!draft) return;
    setNewName(`${draft.name} Copy`);
    setNewId(`${draft.id}_copy`.slice(0, 40));
    setDuplicateOpen(true);
  }

  async function setDefault() {
    if (!draft || draft.default) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/agents/${encodeURIComponent(draft.id)}/default`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(await responseError(response));
      await loadAgents(draft.id);
      setNotice({ tone: "ok", message: text("Default Agent updated", "默认 Agent 已更新") });
    } catch (error) {
      setNotice({ tone: "error", message: String(error) });
    } finally {
      setBusy(false);
    }
  }

  async function removeAgent() {
    if (!draft || deleteId !== draft.id) return;
    setBusy(true);
    try {
      const response = await fetch(`/api/agents/${encodeURIComponent(draft.id)}`, {
        method: "DELETE",
      });
      if (!response.ok) throw new Error(await responseError(response));
      setDeleteOpen(false);
      setDeleteId("");
      await loadAgents();
    } catch (error) {
      setNotice({ tone: "error", message: String(error) });
    } finally {
      setBusy(false);
    }
  }

  async function openPicker(kind: PickerKind) {
    setPicker(kind);
    setCatalog([]);
    setCatalogSearch("");
    setCatalogError("");
    setCatalogLoading(true);
    try {
      if (kind === "programs") {
        const [programResponse, toolResponse, presetResponse] = await Promise.all([
          fetch("/api/programs"),
          fetch("/api/tools"),
          fetch("/api/tool-profiles"),
        ]);
        if (!programResponse.ok || !toolResponse.ok || !presetResponse.ok) {
          throw new Error(text("Unable to load Programs", "无法加载 Programs"));
        }
        const programs = await programResponse.json() as Program[];
        const tools = await toolResponse.json() as Tool[];
        const presetPayload = await presetResponse.json();
        setPresets(presetPayload.profiles || {});
        setCatalog([
          ...tools.map((tool) => ({
            name: tool.name,
            description: tool.description || "",
            group: text("Functions", "函数"),
            available: !tool.disabled,
          })),
          ...programs.map((program) => ({
            name: program.name,
            description: program.description || "",
            group: program.category === "app"
              ? text("Applications", "应用")
              : text("Agentic Functions", "Agentic 函数"),
            available: true,
          })),
        ]);
      } else if (kind === "skills") {
        const response = await fetch("/api/skills");
        if (!response.ok) throw new Error(await responseError(response));
        const rows = await response.json() as Skill[];
        setCatalog(rows.map((skill) => ({
          name: skill.name,
          description: skill.description || "",
          group: skill.category || text("Uncategorized", "未分类"),
          available: skill.enabled !== false,
        })));
      } else {
        const response = await fetch("/api/mcp/servers");
        if (!response.ok) throw new Error(await responseError(response));
        const payload = await response.json();
        const rows = Array.isArray(payload.servers) ? payload.servers as McpServer[] : [];
        setCatalog(rows.map((server) => ({
          name: server.name,
          description: server.status || text("Connected service", "已连接服务"),
          group: text("Connected Services", "已连接服务"),
          available: server.connected !== false && server.enabled !== false,
        })));
      }
    } catch (error) {
      setCatalogError(String(error));
    } finally {
      setCatalogLoading(false);
    }
  }

  const configuredNames = useMemo(() => {
    if (!draft || !picker) return [];
    const available = catalog.filter((item) => item.available).map((item) => item.name);
    if (picker === "programs") {
      if (draft.tools.mode === "automatic") return [...new Set(available)];
      if (draft.tools.mode === "none") return [];
      return selectedProgramNames(draft.tools, presets);
    }
    if (picker === "skills") {
      const mode = policyMode(draft.skills);
      return mode === "all" ? [...new Set(available)] : mode === "none" ? [] : draft.skills.allowed || [];
    }
    const mode = policyMode(draft.mcp);
    return mode === "all" ? [...new Set(available)] : mode === "none" ? [] : draft.mcp.allowed || [];
  }, [catalog, draft, picker, presets]);

  const visibleCatalog = useMemo(() => {
    const query = catalogSearch.trim().toLowerCase();
    const known = new Set(catalog.map((item) => item.name));
    const missing = configuredNames
      .filter((name) => !known.has(name))
      .map((name) => ({
        name,
        description: text("Referenced by this Agent but not available", "此 Agent 引用了该项，但当前不可用"),
        group: text("Missing", "缺失"),
        available: false,
      }));
    return [...catalog, ...missing].filter((item) =>
      !query || item.name.toLowerCase().includes(query) || item.description.toLowerCase().includes(query)
    );
  }, [catalog, catalogSearch, configuredNames, text]);

  const groupedCatalog = useMemo(() => {
    const groups = new Map<string, CatalogItem[]>();
    for (const item of visibleCatalog) {
      groups.set(item.group, [...(groups.get(item.group) || []), item]);
    }
    return [...groups.entries()];
  }, [visibleCatalog]);

  function toggleCatalogName(name: string) {
    if (!draft || !picker) return;
    if (picker === "programs") {
      const base = configuredNames;
      const allowed = base.includes(name) ? base.filter((item) => item !== name) : [...base, name];
      updateDraft({ tools: { mode: "selected", allowed } });
    } else if (picker === "skills") {
      const base = configuredNames;
      const allowed = base.includes(name) ? base.filter((item) => item !== name) : [...base, name];
      updateDraft({ skills: { ...draft.skills, allowed, disabled: draft.skills.disabled.filter((item) => item !== "*") } });
    } else {
      const base = configuredNames;
      const allowed = base.includes(name) ? base.filter((item) => item !== name) : [...base, name];
      updateDraft({ mcp: { ...draft.mcp, allowed, disabled: draft.mcp.disabled.filter((item) => item !== "*") } });
    }
  }

  function toggleRequired(name: string) {
    if (!draft) return;
    const required = draft.mcp.required.includes(name)
      ? draft.mcp.required.filter((item) => item !== name)
      : [...draft.mcp.required, name];
    updateDraft({ mcp: { ...draft.mcp, required } });
  }

  function setGateMode(kind: "skills" | "mcp", mode: "all" | "selected" | "none") {
    if (!draft) return;
    if (kind === "skills") {
      const skills = mode === "none"
        ? { allowed: [], disabled: ["*"], categories: [] }
        : mode === "all"
          ? { allowed: [], disabled: [], categories: [] }
          : { ...draft.skills, disabled: draft.skills.disabled.filter((item) => item !== "*") };
      updateDraft({ skills });
    } else {
      const mcp = mode === "none"
        ? { allowed: [], disabled: ["*"], required: [] }
        : mode === "all"
          ? { allowed: [], disabled: [], required: draft.mcp.required }
          : { ...draft.mcp, disabled: draft.mcp.disabled.filter((item) => item !== "*") };
      updateDraft({ mcp });
    }
  }

  if (loading) {
    return <div className="main"><div className={managePageStyles.view}><ManagePageHeader title={text("Agents", "Agents")} /><div className={managePageStyles.empty}>{text("Loading Agents…", "正在加载 Agents…")}</div></div></div>;
  }

  return (
    <div className="main">
      <div className={managePageStyles.view}>
        <ManagePageHeader
          title={text("Agents", "Agents")}
          tabs={TABS.map((item) => ({ id: item.id, label: text(item.en, item.zh) }))}
          activeTab={tab}
          onTabChange={(id) => setTab(id as TabId)}
          toolbar={dirty ? <span className={styles.unsaved}>{text("Unsaved changes", "有未保存的修改")}</span> : null}
          actions={[
            {
              label: text("New Agent", "新建 Agent"),
              onClick: () => { setNewName(""); setNewId(""); setCreateOpen(true); },
            },
            {
              label: busy ? text("Working…", "处理中…") : dirty ? text("Save changes", "保存修改") : text("Saved", "已保存"),
              onClick: () => { void save(); },
              primary: true,
              disabled: !draft || !dirty || busy,
            },
          ]}
        />

        {notice ? (
          <div className={notice.tone === "error" ? styles.errorBanner : styles.successBanner} role={notice.tone === "error" ? "alert" : "status"}>
            {notice.tone === "error" ? <AlertCircle size={15} /> : <Check size={15} />}
            <span>{notice.message}</span>
            {notice.tone === "error" && baseline ? <button onClick={() => void loadAgents(baseline.id)}>{text("Reload", "重新加载")}</button> : null}
          </div>
        ) : null}

        <div className={managePageStyles.splitBody}>
          <aside className={styles.agentNav} aria-label={text("Agent list", "Agent 列表")}>
            <div className={styles.agentList}>
              {agents.map((agent) => (
                <ManageRow
                  key={agent.id}
                  className={agent.id === selectedId ? styles.agentSelected : undefined}
                  onClick={() => chooseAgent(agent)}
                  icon={<BotIcon size={16} />}
                  name={agent.name || agent.id}
                  description={agent.id}
                  meta={agent.default ? <span className={managePageStyles.badge}>{text("Default", "默认")}</span> : null}
                />
              ))}
            </div>
          </aside>

          <main className={styles.editor}>
            {!draft ? (
              <div className={styles.emptyState}>
                <BotIcon size={24} />
                <h2>{text("Create your first Agent", "创建第一个 Agent")}</h2>
                <p>{text("An Agent keeps one configuration across all six sections.", "一个 Agent 在六个配置区共用同一份配置。")}</p>
                <Button onClick={() => setCreateOpen(true)}><Plus size={15} />{text("New Agent", "新建 Agent")}</Button>
              </div>
            ) : (
              <div className={settingsStyles.page}>
                <div className={`${settingsStyles.pageHeader} ${styles.agentPageHeader}`}>
                  <span className={styles.detailAvatar}><BotIcon size={20} /></span>
                  <div className={styles.agentHeading}>
                    <h2 className={settingsStyles.pageTitle}>{draft.name || draft.id}</h2>
                    <p className={settingsStyles.pageMeta}>{draft.id}</p>
                  </div>
                  {draft.default ? <span className={managePageStyles.badge}>{text("Default", "默认")}</span> : null}
                  <details className={styles.moreMenu}>
                    <summary aria-label={text("Agent actions", "Agent 操作")}><MoreHorizontal size={18} /></summary>
                    <div>
                      <button disabled={draft.default || busy} onClick={() => void setDefault()}><Check size={14} />{text("Set as default", "设为默认")}</button>
                      <button disabled={busy} onClick={openDuplicate}><Copy size={14} />{text("Duplicate Agent", "复制 Agent")}</button>
                      <button onClick={() => setTab("sessions")}><FileText size={14} />{text("Workspace files", "Workspace 文件")}</button>
                      <button className={styles.danger} disabled={draft.default || busy} onClick={() => { setDeleteId(""); setDeleteOpen(true); }}><Trash2 size={14} />{text("Delete Agent", "删除 Agent")}</button>
                    </div>
                  </details>
                </div>
                <div className={settingsStyles.pageBody} role="tabpanel">
                  <div className={styles.panel}>
                  {tab === "overview" ? <OverviewPanel draft={draft} update={updateDraft} text={text} /> : null}
                  {tab === "model" ? <ModelPanel draft={draft} update={updateDraft} text={text} /> : null}
                  {tab === "programs" ? (
                    <ProgramsPanel draft={draft} update={updateDraft} presets={presets} openPicker={() => void openPicker("programs")} text={text} />
                  ) : null}
                  {tab === "skills" ? (
                    <GatePanel kind="skills" policy={draft.skills} setMode={(mode) => { setGateMode("skills", mode); if (mode === "selected") void openPicker("skills"); }} openPicker={() => void openPicker("skills")} text={text} />
                  ) : null}
                  {tab === "mcp" ? (
                    <GatePanel kind="mcp" policy={draft.mcp} setMode={(mode) => { setGateMode("mcp", mode); if (mode === "selected") void openPicker("mcp"); }} openPicker={() => void openPicker("mcp")} text={text} />
                  ) : null}
                  {tab === "sessions" ? <SessionsPanel draft={draft} update={updateDraft} workspace={workspace} text={text} /> : null}
                  </div>
                </div>
              </div>
            )}
          </main>
        </div>
      </div>

      <AgentDialog
        open={createOpen || duplicateOpen}
        duplicate={duplicateOpen}
        name={newName}
        id={newId}
        busy={busy}
        setName={setNewName}
        setId={setNewId}
        close={() => { setCreateOpen(false); setDuplicateOpen(false); }}
        submit={() => void createOrDuplicate(duplicateOpen)}
        text={text}
      />

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{text("Delete Agent", "删除 Agent")}</DialogTitle>
            <DialogDescription>{text("This permanently removes its configuration, workspace, and sessions. Global Programs, Skills, and MCP servers are not deleted.", "这会永久删除该 Agent 的配置、workspace 和 sessions，不会删除全局 Programs、Skills 或 MCP 服务。")}</DialogDescription>
          </DialogHeader>
          <label className={styles.dialogField}>{text(`Type ${draft?.id || ""} to confirm`, `输入 ${draft?.id || ""} 以确认`)}<input value={deleteId} onChange={(event) => setDeleteId(event.target.value)} /></label>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteOpen(false)}>{text("Cancel", "取消")}</Button>
            <Button variant="destructive" disabled={busy || deleteId !== draft?.id} onClick={() => void removeAgent()}>{text("Delete Agent", "删除 Agent")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CatalogDialog
        kind={picker}
        open={picker !== null}
        close={() => setPicker(null)}
        loading={catalogLoading}
        error={catalogError}
        search={catalogSearch}
        setSearch={setCatalogSearch}
        groups={groupedCatalog}
        selected={new Set(configuredNames)}
        required={new Set(draft?.mcp.required || [])}
        toggle={toggleCatalogName}
        toggleRequired={toggleRequired}
        text={text}
      />
    </div>
  );
}

type PanelProps = {
  draft: Agent;
  update: (patch: Partial<Agent>) => void;
  text: (english: string, chinese: string) => string;
};

function PanelIntro({ title, description }: { title: string; description: string }) {
  return <div className={styles.panelIntro}><h3>{title}</h3><p>{description}</p></div>;
}

function OverviewPanel({ draft, update, text }: PanelProps) {
  return <>
    <PanelIntro title={text("Overview", "概览")} description={text("Identity and routing names used across the product.", "配置产品内显示身份与消息路由名称。")}/>
    <div className={styles.formGrid}>
      <label>{text("Display name", "显示名称")}<input value={draft.name} onChange={(event) => update({ name: event.target.value })}/></label>
      <label>{text("Agent ID", "Agent ID")}<input value={draft.id} disabled/><small>{text("Cannot be changed after creation.", "创建后不可修改。")}</small></label>
      <label>{text("Channel identity", "频道身份")}<input value={draft.identity.name} onChange={(event) => update({ identity: { ...draft.identity, name: event.target.value } })}/></label>
      <label>{text("Mention patterns", "提及规则")}<input value={draft.identity.mention_patterns.join(", ")} onChange={(event) => update({ identity: { ...draft.identity, mention_patterns: csv(event.target.value) } })}/><small>{text("Comma-separated patterns.", "使用逗号分隔。")}</small></label>
    </div>
    <div className={styles.summaryGrid}>
      <SummaryCard label={text("Model", "模型")} value={draft.model.id || text("Inherited", "继承默认值")} detail={draft.model.provider || text("Current provider", "当前 provider")}/>
      <SummaryCard label="Programs" value={draft.tools.mode === "automatic" ? text("All Programs", "全部 Programs") : draft.tools.mode === "none" ? text("No Programs", "不使用 Programs") : text("Selected scope", "指定范围")} detail={text("Schemas load on demand", "Schema 按需加载")}/>
      <SummaryCard label={text("Session scope", "会话范围")} value={draft.session_scope} detail={`${draft.session_idle_minutes} min`}/>
    </div>
  </>;
}

function ModelPanel({ draft, update, text }: PanelProps) {
  return <>
    <PanelIntro title={text("Model & Instructions", "模型与指令")} description={text("Select the default model and persistent instructions for this Agent.", "选择此 Agent 的默认模型与持久指令。")}/>
    <div className={styles.formGrid}>
      <label>{text("Provider", "Provider")}<input value={draft.model.provider} placeholder={text("Inherit current provider", "继承当前 provider")} onChange={(event) => update({ model: { ...draft.model, provider: event.target.value } })}/></label>
      <label>{text("Model", "模型")}<input value={draft.model.id} placeholder={text("Inherit current model", "继承当前模型")} onChange={(event) => update({ model: { ...draft.model, id: event.target.value } })}/></label>
      <label>{text("Thinking effort", "思考强度")}<select value={draft.thinking_effort} onChange={(event) => update({ thinking_effort: event.target.value })}><option value="minimal">Minimal</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="xhigh">XHigh</option></select></label>
      <label className={styles.fullField}>{text("System prompt", "系统指令")}<textarea rows={10} value={draft.system_prompt} onChange={(event) => update({ system_prompt: event.target.value })}/><small>{text("Workspace prompt files remain separate and appear under Sessions.", "Workspace 指令文件保持独立，在 Sessions 中查看。")}</small></label>
    </div>
  </>;
}

function ProgramsPanel({ draft, update, presets, openPicker, text }: PanelProps & { presets: Record<string, string[]>; openPicker: () => void }) {
  const selected = selectedProgramNames(draft.tools, presets);
  return <>
    <PanelIntro title="Programs" description={text("Control callable Functions, Agentic Functions, and Applications.", "控制可调用的 Functions、Agentic Functions 与 Applications。")}/>
    <div className={styles.modeGrid}>
      {([
        ["automatic", text("All Programs", "全部 Programs"), text("All available Programs; schemas load only when needed.", "允许全部可用 Programs，schema 仅在需要时加载。")],
        ["selected", text("Selected scope", "指定范围"), text("Use an Access preset or explicit names.", "使用 Access preset 或明确名称。")],
        ["none", text("No Programs", "不使用 Programs"), text("Run without callable Programs.", "不提供可调用 Programs。")],
      ] as const).map(([mode, title, description]) => <label key={mode} className={draft.tools.mode === mode ? styles.modeActive : styles.modeCard}><input type="radio" name="program-mode" checked={draft.tools.mode === mode} onChange={() => update({ tools: mode === "selected" ? { mode, allowed: selected } : { mode } })}/><span><strong>{title}</strong><small>{description}</small></span></label>)}
    </div>
    {draft.tools.mode === "selected" ? <label className={styles.presetField}>{text("Access preset", "Access preset")}<select value={draft.tools.preset || "__custom__"} onChange={(event) => update({ tools: event.target.value === "__custom__" ? { mode: "selected", allowed: selected } : { mode: "selected", preset: event.target.value } })}><option value="__custom__">{text("Custom selection", "自定义选择")}</option>{draft.tools.preset && !presets[draft.tools.preset] ? <option value={draft.tools.preset}>{draft.tools.preset}</option> : null}{Object.keys(presets).sort().map((name) => <option key={name} value={name}>{name}</option>)}</select></label> : null}
    <ScopeCard title={draft.tools.mode === "automatic" ? text("All Programs", "全部 Programs") : draft.tools.mode === "none" ? text("Programs disabled", "Programs 已禁用") : draft.tools.preset ? `${text("Preset", "Preset")} · ${draft.tools.preset}` : text(`${selected.length} selected`, `已选择 ${selected.length} 项`)} description={text("The full catalog is requested only when you open the selector.", "完整目录只在打开选择器时加载。") } action={text("Browse programs…", "浏览 Programs…")} onAction={openPicker}/>
  </>;
}

function GatePanel({ kind, policy, setMode, openPicker, text }: { kind: "skills" | "mcp"; policy: GatePolicy | McpPolicy; setMode: (mode: "all" | "selected" | "none") => void; openPicker: () => void; text: (english: string, chinese: string) => string }) {
  const mode = policyMode(policy);
  const noun = kind === "skills" ? "Skills" : "MCP";
  const count = policy.allowed?.length || 0;
  const required = kind === "mcp" ? (policy as McpPolicy).required.length : 0;
  return <>
    <PanelIntro title={noun} description={kind === "skills" ? text("Control which Skills this Agent may discover. Skill content stays in the Skills catalog.", "控制此 Agent 可发现的 Skills；Skill 内容仍由 Skills 页面管理。") : text("Control allowed and required MCP services. Credentials stay in MCP Servers.", "控制允许与必需的 MCP 服务；凭据仍由 MCP Servers 管理。")}/>
    <div className={styles.modeGrid}>
      {(["all", "selected", "none"] as const).map((value) => <label key={value} className={mode === value ? styles.modeActive : styles.modeCard}><input type="radio" name={`${kind}-mode`} checked={mode === value} onChange={() => setMode(value)}/><span><strong>{value === "all" ? text(`All ${noun}`, `全部 ${noun}`) : value === "selected" ? text("Selected scope", "指定范围") : text(`No ${noun}`, `不使用 ${noun}`)}</strong><small>{value === "all" ? text("Use all globally available entries.", "使用全部全局可用项。") : value === "selected" ? text("Keep explicit stable references.", "保存明确的稳定引用。") : text("Disable this capability for the Agent.", "为此 Agent 禁用该能力。")}</small></span></label>)}
    </div>
    <ScopeCard title={mode === "all" ? text(`All ${noun}`, `全部 ${noun}`) : mode === "none" ? text(`${noun} disabled`, `${noun} 已禁用`) : text(`${count} selected`, `已选择 ${count} 项`)} description={kind === "mcp" ? text(`${required} required services. Missing required services prevent startup.`, `${required} 个必需服务；缺失时会阻止 Agent 启动。`) : text("Missing references are retained until you remove and save them.", "失效引用会保留，直到用户删除并保存。") } action={text(`Manage ${noun}…`, `管理 ${noun}…`)} onAction={openPicker}/>
  </>;
}

function SessionsPanel({ draft, update, workspace, text }: PanelProps & { workspace: Workspace | null }) {
  async function copyPath(path: string) {
    await navigator.clipboard.writeText(path);
  }
  return <>
    <PanelIntro title={text("Sessions", "会话")} description={text("Configure conversation isolation, reset rules, and workspace files.", "配置会话隔离、重置规则与 workspace 文件。")}/>
    <div className={styles.formGrid}>
      <label className={styles.fullField}>{text("Session scope", "会话范围")}<select value={draft.session_scope} onChange={(event) => update({ session_scope: event.target.value })}><option value="per-account-channel-peer">Per account + channel + peer</option><option value="per-channel-peer">Per channel + peer</option><option value="per-peer">Per peer</option><option value="main">Shared main session</option></select></label>
      <label>{text("Idle reset (minutes)", "空闲重置（分钟）")}<input type="number" min={0} max={525600} value={draft.session_idle_minutes} onChange={(event) => update({ session_idle_minutes: Number(event.target.value) })}/><small>{text("0 disables idle reset.", "0 表示禁用空闲重置。")}</small></label>
      <label>{text("Daily reset", "每日重置")}<input type="time" value={draft.session_daily_reset} onChange={(event) => update({ session_daily_reset: event.target.value })}/><small>{text("Empty disables daily reset.", "留空表示禁用。")}</small></label>
    </div>
    <section className={styles.workspaceSection}><div><h4>Workspace</h4><p>{workspace?.path || text("Loading workspace…", "正在加载 workspace…")}</p></div>{workspace?.files.map((file) => <div className={styles.workspaceFile} key={file.name}><span><FileText size={16}/></span><div><strong>{file.name}</strong><small>{file.exists ? file.path : text("Optional file not created", "可选文件尚未创建")}</small></div><button onClick={() => void copyPath(file.path)}>{text("Copy path", "复制路径")}</button></div>)}</section>
  </>;
}

function SummaryCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className={styles.summaryCard}><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function ScopeCard({ title, description, action, onAction }: { title: string; description: string; action: string; onAction: () => void }) {
  return <section className={styles.scopeCard}><div><h4>{title}</h4><p>{description}</p></div><Button variant="outline" onClick={onAction}>{action}</Button></section>;
}

function AgentDialog({ open, duplicate, name, id, busy, setName, setId, close, submit, text }: { open: boolean; duplicate: boolean; name: string; id: string; busy: boolean; setName: (value: string) => void; setId: (value: string) => void; close: () => void; submit: () => void; text: (english: string, chinese: string) => string }) {
  return <Dialog open={open} onOpenChange={(value) => { if (!value) close(); }}><DialogContent><DialogHeader><DialogTitle>{duplicate ? text("Duplicate Agent", "复制 Agent") : text("Create Agent", "创建 Agent")}</DialogTitle><DialogDescription>{text("The ID is permanent. Model, instructions, and capabilities can be configured after creation.", "ID 创建后不可修改；模型、指令与能力可在创建后配置。")}</DialogDescription></DialogHeader><label className={styles.dialogField}>{text("Display name", "显示名称")}<input value={name} onChange={(event) => setName(event.target.value)}/></label><label className={styles.dialogField}>{text("Agent ID", "Agent ID")}<input autoFocus value={id} pattern="[a-z][a-z0-9_-]{0,39}" onChange={(event) => setId(event.target.value)}/><small>{text("Lowercase letters, numbers, underscores, and hyphens; start with a letter.", "小写字母开头，只允许小写字母、数字、下划线和连字符。")}</small></label><DialogFooter><Button variant="ghost" onClick={close}>{text("Cancel", "取消")}</Button><Button disabled={busy || !/^[a-z][a-z0-9_-]{0,39}$/.test(id)} onClick={submit}>{duplicate ? text("Duplicate", "复制") : text("Create Agent", "创建 Agent")}</Button></DialogFooter></DialogContent></Dialog>;
}

function CatalogDialog({ kind, open, close, loading, error, search, setSearch, groups, selected, required, toggle, toggleRequired, text }: { kind: PickerKind | null; open: boolean; close: () => void; loading: boolean; error: string; search: string; setSearch: (value: string) => void; groups: Array<[string, CatalogItem[]]>; selected: Set<string>; required: Set<string>; toggle: (name: string) => void; toggleRequired: (name: string) => void; text: (english: string, chinese: string) => string }) {
  const title = kind === "programs" ? "Programs" : kind === "skills" ? "Skills" : "MCP";
  return <Dialog open={open} onOpenChange={(value) => { if (!value) close(); }}><DialogContent className={styles.catalogDialog}><DialogHeader><DialogTitle>{text(`Manage ${title}`, `管理 ${title}`)}</DialogTitle><DialogDescription>{text("The catalog is loaded only while this selector is open. Missing stored references remain visible.", "目录只在此选择器打开时加载；已保存但失效的引用仍会显示。")}</DialogDescription></DialogHeader><label className={styles.searchField}><Search size={15}/><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={text(`Search ${title}…`, `搜索 ${title}…`)}/></label><div className={styles.catalogBody}>{loading ? <div className={styles.catalogState}>{text("Loading catalog…", "正在加载目录…")}</div> : error ? <div className={styles.catalogState}>{error}</div> : groups.length === 0 ? <div className={styles.catalogState}>{text("No matching entries", "没有匹配项")}</div> : groups.map(([group, items]) => <section className={styles.catalogGroup} key={group}><h4>{group}<span>{items.length}</span></h4>{items.map((item) => <div className={styles.catalogRow} key={`${group}:${item.name}`}><label><input type="checkbox" checked={selected.has(item.name)} onChange={() => toggle(item.name)}/><span><strong>{item.name}</strong><small>{item.description || text("No description", "暂无描述")}</small></span></label><span className={item.available ? styles.available : styles.missing}>{item.available ? text("Available", "可用") : text("Missing", "缺失")}</span>{kind === "mcp" && selected.has(item.name) ? <button className={required.has(item.name) ? styles.requiredActive : styles.requiredButton} onClick={() => toggleRequired(item.name)}>{required.has(item.name) ? text("Required", "必需") : text("Optional", "可选")}</button> : null}</div>)}</section>)}</div><DialogFooter><Button onClick={close}>{text("Done", "完成")}</Button></DialogFooter></DialogContent></Dialog>;
}
