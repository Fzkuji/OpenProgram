"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { useQueryClient } from "@tanstack/react-query";
import { AGENTS_KEY } from "@/lib/agents/agents-query";
import { BotIcon, CheckIcon, MessageCircleIcon, PlusIcon, SearchIcon, SettingsIcon } from "@/components/animated-icons";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ManageEmptyState, ManagePageHeader, managePageStyles, ManageLoadingState } from "@/components/ui/manage-page";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { MenuRow } from "@/components/ui/menu/menu-parts";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { HoverTip } from "@/components/ui/tooltip";
import { MENU_ITEM_STATES, MENU_PANEL, MENU_SEPARATOR, itemCls } from "@/components/ui/menu/menu-styles";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";
import { HttpError, jsonFetch } from "@/lib/net/fetch-client";
import { startAgentConversation } from "@/lib/agents/start-conversation";
import settingsStyles from "../settings/settings-page.module.css";
import { AgentAvatar } from "./agent-avatar";
import { AgentButton } from "./agent-controls";
import { RailBadge, RailGroupLabel, RailItem, RailSearch, RailSplit, RailToggle, SecondaryRail } from "@/components/ui/secondary-rail";
import { Input } from "@/components/ui/input";
import { SearchInput } from "@/components/ui/search-input";
import { AGENT_SETTINGS_FRAME, AgentChips, AgentGrid, agentFrame, MORPH_CONTENT, SETTINGS_FRAME_MOTION, SettingsWidthHandle } from "./agent-overview";
import { AdvancedPanel, ContextPanel, MemoryPanel, OverviewPanel } from "./agent-panels";
import { CapabilityPanel } from "./agent-capabilities";
import { AlwaysOnPanel } from "./always-on-panel";
import { dailyBudgetValid, scheduleValid, wakeMinutesValid } from "@/lib/agents/always-on";
import { ModelPicker, selectedModel, useAgentModels, type ModelSelection } from "./model-picker";
import { clone, configuration, normalizeAgent, trialConfiguration, TABS, type Agent, type TabId } from "./agent-types";
import styles from "./agents-page.module.css";

const AGENTS_RAIL = "agents";

type CachedDraft = { draft: Agent; baseline: Agent; tab: TabId };
// Keep unsaved edits across in-app navigation, including a trial conversation.
// This is deliberately memory-only; a page reload uses the beforeunload guard.
const cachedDrafts = new Map<string, CachedDraft>();
let lastSelected = "";
type PendingAction = { run: (saved: Agent | null) => void | Promise<void> };
function forgetAgent(id: string) { cachedDrafts.delete(id); if (lastSelected === id) lastSelected = ""; }
function changed(a: Agent, b: Agent) { return JSON.stringify(configuration(a)) !== JSON.stringify(configuration(b)); }
function errorMessage(error: unknown) { return error instanceof Error ? error.message : String(error); }

export function AgentsPage() {
  const { text } = useTranslation();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [baseline, setBaseline] = useState<Agent | null>(null);
  const [draft, setDraft] = useState<Agent | null>(null);
  const [tab, setTab] = useState<TabId>("overview");
  // The page always opens on the card grid; the full editor is one step in.
  const [view, setView] = useState<"grid" | "editor">("grid");
  const [quickOpen, setQuickOpen] = useState(false);
  const reduceMotion = useReducedMotion();
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ tone: "ok" | "error"; message: string } | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [conflict, setConflict] = useState<Agent | null>(null);
  const [dialog, setDialog] = useState<"create" | "duplicate" | "delete" | null>(null);
  const [newName, setNewName] = useState("");
  const [newModel, setNewModel] = useState<ModelSelection>({ model: { provider: "", id: "" }, thinking_effort: "" });
  const [deleteId, setDeleteId] = useState("");
  const [dialogError, setDialogError] = useState("");
  const mounted = useRef(true);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const dialogOpener = useRef<HTMLElement | null>(null);
  const busyRef = useRef(false);
  const models = useAgentModels();
  const creationModel = selectedModel(newModel, models);
  const creationEffortInvalid = Boolean(newModel.thinking_effort && !creationModel?.thinking_levels?.includes(newModel.thinking_effort));
  const dirty = Boolean(draft && baseline && changed(draft, baseline));
  const model = draft ? selectedModel(draft, models) : undefined;
  const effortInvalid = Boolean(draft?.thinking_effort && model && !model.thinking_levels?.includes(draft.thinking_effort));
  const formError = draft && !draft.name.trim() ? text("Enter an Agent name.", "请输入 Agent 名称。")
    : effortInvalid && dirty ? text("Choose a supported thinking effort in General.", "请在常规分区选择支持的思考强度。")
    : draft && !wakeMinutesValid(draft.always_on.wake_minutes) ? text("Enter a wake interval from 5 to 10080 minutes in Always-on.", "请在常驻分区填写 5 到 10080 分钟的唤醒间隔。")
    : draft && !scheduleValid(draft.always_on) ? text("Give quiet hours in Always-on a different start and end time.", "请为常驻分区的安静时段填写不同的开始和结束时间。")
    : draft && !dailyBudgetValid(draft.always_on.daily_budget_usd) ? text("Enter a daily check-in budget above $0 in Always-on, or leave it empty.", "请在常驻分区填写大于 0 美元的每日巡检预算，或留空。") : "";

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (draft && baseline) {
      cachedDrafts.set(draft.id, { draft: clone(draft), baseline: clone(baseline), tab });
      lastSelected = draft.id;
    }
  }, [draft, baseline, tab]);
  useEffect(() => {
    if (!dirty) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);

  const applyAgent = useCallback((raw: Agent, restore = false) => {
    const saved = normalizeAgent(clone(raw));
    setAgents((rows) => rows.map((agent) => agent.id === saved.id ? saved : agent));
    const cached = restore ? cachedDrafts.get(saved.id) : undefined;
    if (cached && changed(cached.draft, cached.baseline)) {
      setBaseline(clone(cached.baseline)); setDraft(clone(cached.draft)); setTab(cached.tab);
      setConflict(saved.revision !== cached.baseline.revision ? saved : null);
    } else {
      setBaseline(saved); setDraft(clone(saved)); setTab("overview"); setConflict(null);
      cachedDrafts.set(saved.id, { draft: clone(saved), baseline: clone(saved), tab: "overview" });
    }
    setNotice(null); lastSelected = saved.id;
  }, []);
  const loadAgents = useCallback(async (preferredId?: string, signal?: AbortSignal) => {
    const data = await jsonFetch<{ agents: Agent[] }>("/api/agents", { signal });
    if (!mounted.current || signal?.aborted) return;
    const rows = data.agents.map(normalizeAgent);
    setAgents(rows); setLoadError("");
    const next = rows.find((agent) => agent.id === (preferredId || lastSelected)) || rows.find((agent) => agent.default) || rows[0];
    if (next) applyAgent(next, true);
    else { setDraft(null); setBaseline(null); setConflict(null); lastSelected = ""; }
  }, [applyAgent]);
  // Everything else that names an Agent (the composer chip, a message, a
  // binding row) reads the shared list, so a save here shows there at once.
  const queryClient = useQueryClient();
  useEffect(() => { if (agents.length) queryClient.setQueryData(AGENTS_KEY, agents); }, [agents, queryClient]);
  useEffect(() => {
    const controller = new AbortController();
    void loadAgents(undefined, controller.signal).catch((error) => { if (!controller.signal.aborted) setLoadError(errorMessage(error)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [loadAgents]);
  function updateDraft(patch: Partial<Agent>) { setDraft((current) => current ? { ...current, ...patch } : current); setNotice(null); }
  function lock(value: boolean) { busyRef.current = value; if (mounted.current) setBusy(value); }
  function discard() {
    if (!baseline) return;
    setDraft(clone(baseline)); setConflict(null); setNotice(null);
    cachedDrafts.set(baseline.id, { draft: clone(baseline), baseline: clone(baseline), tab });
  }
  async function save(): Promise<Agent | null> {
    if (!draft || busyRef.current || formError) return null;
    const submitted = clone(draft);
    const submittedCache = cachedDrafts.get(submitted.id);
    captureDialogOpener(); lock(true); setNotice(null);
    try {
      const result = await jsonFetch<{ agent: Agent }>(`/api/agents/${encodeURIComponent(submitted.id)}`, {
        method: "PATCH", body: JSON.stringify({ ...configuration(submitted), expected_revision: baseline?.revision ?? submitted.revision }),
      });
      const saved = normalizeAgent(result.agent);
      // A remounted editor may already own a newer draft. Keep its baseline
      // so the existing revision conflict flow reconciles this late response.
      if (cachedDrafts.get(saved.id) === submittedCache) {
        cachedDrafts.set(saved.id, { draft: clone(saved), baseline: clone(saved), tab });
      }
      if (mounted.current) {
        setAgents((rows) => rows.map((agent) => agent.id === saved.id ? saved : agent));
        setBaseline(clone(saved)); setDraft(clone(saved)); setConflict(null);
        setNotice({ tone: "ok", message: text("Saved", "已保存") });
      }
      return saved;
    } catch (error) {
      if (!mounted.current) return null;
      if (error instanceof HttpError && error.status === 409) {
        try {
          const body = JSON.parse(error.body || "{}");
          const latest = body.agent || body.current || (await jsonFetch<{ agent: Agent }>(`/api/agents/${encodeURIComponent(submitted.id)}`)).agent;
          if (mounted.current) { setConflict(normalizeAgent(latest)); setPending(null); }
        } catch (refreshError) { if (mounted.current) setNotice({ tone: "error", message: errorMessage(refreshError) }); }
      } else setNotice({ tone: "error", message: errorMessage(error) });
      return null;
    } finally { lock(false); }
  }
  function captureDialogOpener() {
    const active = document.activeElement as HTMLElement | null;
    // A menu item closes with its menu; focus goes back to the menu button.
    dialogOpener.current = active?.closest("[role=menu]") ? menuTrigger.current : active;
  }
  function restoreDialogFocus(event: Event) {
    event.preventDefault();
    if (document.querySelector('[role="dialog"][data-state="open"]')) return;
    const target = dialogOpener.current;
    if (target?.isConnected && !target.matches(":disabled")) target.focus();
    else document.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus();
  }
  function guard(run: PendingAction["run"]) {
    if (busyRef.current) return;
    if (dirty) { captureDialogOpener(); setPending({ run }); } else void run(baseline);
  }
  async function resolveLeave(choice: "save" | "discard") {
    if (!pending || busyRef.current) return;
    const action = pending;
    const saved = choice === "save" ? await save() : baseline;
    if (choice === "save" && !saved) return;
    if (choice === "discard") discard();
    setPending(null); await action.run(saved);
  }
  function openDialog(kind: NonNullable<typeof dialog>) {
    captureDialogOpener();
    guard((saved) => {
      setNewModel({ model: { provider: "", id: "" }, thinking_effort: "" });
      setDialogError(""); setDeleteId(""); setNewName(kind === "duplicate" && saved ? `${saved.name} ${text("Copy", "副本")}` : ""); setDialog(kind);
    });
  }
  async function submitName() {
    if (!newName.trim() || busyRef.current) return;
    const duplicate = dialog === "duplicate";
    if (!duplicate && creationEffortInvalid) return;
    if (duplicate && !baseline) return;
    lock(true); setDialogError("");
    try {
      const url = duplicate ? `/api/agents/${encodeURIComponent(baseline!.id)}/duplicate` : "/api/agents";
      const body = duplicate ? { name: newName.trim() } : { name: newName.trim(), ...newModel };
      const payload = await jsonFetch<{ agent: Agent }>(url, { method: "POST", body: JSON.stringify(body) });
      if (!mounted.current) return;
      const created = normalizeAgent(payload.agent); setAgents((rows) => [...rows, created]); applyAgent(created); setQuickOpen(true);
      setDialog(null); setNewName(""); setSearch("");
    } catch (error) { if (mounted.current) setDialogError(errorMessage(error)); }
    finally { lock(false); }
  }
  async function makeDefault(saved: Agent | null) {
    if (!saved || saved.default || busyRef.current) return;
    lock(true); setNotice(null);
    try {
      const result = await jsonFetch<{ agent: Agent }>(`/api/agents/${encodeURIComponent(saved.id)}/default`, { method: "POST" });
      if (!mounted.current) return;
      const updated = normalizeAgent(result.agent);
      setAgents((rows) => rows.map((agent) => agent.id === updated.id ? updated : { ...agent, default: false }));
      applyAgent(updated); setNotice({ tone: "ok", message: text("Default Agent updated", "默认 Agent 已更新") });
    } catch (error) { if (mounted.current) setNotice({ tone: "error", message: errorMessage(error) }); }
    finally { lock(false); }
  }
  async function remove() {
    if (!baseline || baseline.default || deleteId !== baseline.id || busyRef.current) return;
    const id = baseline.id; lock(true); setDialogError("");
    try {
      await jsonFetch(`/api/agents/${encodeURIComponent(id)}`, { method: "DELETE" });
      forgetAgent(id);
      if (!mounted.current) return;
      setDialog(null); setDeleteId(""); setDraft(null); setBaseline(null); setConflict(null); setQuickOpen(false); setView("grid");
      await loadAgents();
    } catch (error) { if (mounted.current) setDialogError(errorMessage(error)); }
    finally { lock(false); }
  }
  async function start(trial: boolean, saved: Agent | null = baseline) {
    const source = trial ? draft : saved;
    if (!source || busyRef.current) return;
    lock(true); setNotice(null);
    // Capture before navigation. The map also retains this draft on return.
    if (draft && baseline) cachedDrafts.set(draft.id, { draft: clone(draft), baseline: clone(baseline), tab });
    try {
      await startAgentConversation(trial ? { agentId: source.id, config: trialConfiguration(source), trial: true } : { agentId: source.id });
    } catch (error) { if (mounted.current) setNotice({ tone: "error", message: errorMessage(error) }); }
    finally { lock(false); }
  }
  function keepDraft() {
    if (!conflict || !draft) return;
    const current = conflict;
    setBaseline(clone(current)); setDraft({ ...draft, revision: current.revision, updated_at: current.updated_at, default: current.default });
    setAgents((rows) => rows.map((agent) => agent.id === current.id ? current : agent)); setConflict(null);
    setNotice({ tone: "ok", message: text("Draft kept. Review the fields, then save to replace the server configuration.", "已保留草稿。确认字段后保存，将替换服务端配置。") });
  }
  const visible = agents.filter((agent) => `${agent.name} ${agent.id} ${agent.description}`.toLowerCase().includes(search.trim().toLowerCase()));
  const activeName = draft?.name || draft?.id || "";
  const canStart = !models.loading && !models.error && Boolean(model) && !effortInvalid;
  function openAgent(agent: Agent) {
    if (agent.id === draft?.id) { setQuickOpen(true); return; }
    guard(() => { applyAgent(agent, true); setQuickOpen(true); });
  }
  function openFull(next: TabId) { setTab(next); setQuickOpen(false); setView("editor"); }
  // Wide settings show the Agents as a list beside them; narrow settings
  // leave room for the cards. One toggle switches the two widths.
  function showGrid() { setView("grid"); setQuickOpen(Boolean(draft)); }
  // The quick panel and the editor share one frame: opening full settings
  // grows the panel into the editor, collapsing shrinks it back.
  const settingsFrame = reduceMotion ? undefined : AGENT_SETTINGS_FRAME;
  // The header holds the search while the cards show, as every other
  // management page does; the editor's list has a search of its own.
  const gridView = view === "grid" || !draft;
  return <div className={`main ${styles.agentsMain}`}><div className={`${managePageStyles.view} ${styles.agentView}`}>
    <ManagePageHeader title="Agents" railToggle={view === "editor" ? <RailToggle railKey={AGENTS_RAIL} controls="agents-rail" label={{ collapse: text("Collapse Agent list", "收起 Agent 列表"), expand: text("Expand Agent list", "展开 Agent 列表") }} /> : undefined}
      toolbar={gridView && !loading && !loadError && agents.length ? <SearchInput className="min-w-[96px] flex-1 min-[761px]:flex-none min-[761px]:min-w-[140px] min-[761px]:w-[clamp(150px,24vw,280px)]" aria-label={text("Search Agents", "搜索 Agents")} value={search} onChange={setSearch} placeholder={text("Find an Agent…", "查找 Agent…")} /> : undefined}
      actions={[{ label: text("New Agent", "新建 Agent"), onClick: () => openDialog("create"), icon: PlusIcon, disabled: busy || loading || Boolean(loadError), primary: true }]} />
    {notice ? <div className={notice.tone === "error" ? styles.errorBanner : styles.successBanner} role={notice.tone === "error" ? "alert" : "status"}><span>{notice.message}</span></div> : null}
    {loading ? <ManageLoadingState>{text("Loading Agents…", "正在加载 Agents…")}</ManageLoadingState> : loadError ? <div className={styles.centerState} role="alert"><ManageEmptyState icon={<BotIcon size={20} />} title={text("Unable to load Agents", "无法加载 Agents")} description={loadError} action={<AgentButton variant="elevated" onClick={() => { setLoading(true); void loadAgents().catch((error) => setLoadError(errorMessage(error))).finally(() => setLoading(false)); }}>{text("Retry", "重试")}</AgentButton>} /></div> : !agents.length ? <div className={styles.centerState}><ManageEmptyState compact icon={<BotIcon size={20} />} title={text("Create your first Agent", "创建第一个 Agent")} description={text("Configure a model, capabilities, and optional memory.", "配置模型、能力和可选的长期记忆。")} action={<AgentButton icon={PlusIcon} onClick={() => openDialog("create")}>{text("New Agent", "新建 Agent")}</AgentButton>} /></div> : gridView ? <div key="grid" className={styles.viewFill}>
      <AgentGrid agents={agents} search={search} quickOpen={quickOpen} onOpen={openAgent}
        draft={draft} catalog={models} text={text} dirty={dirty} busy={busy} formError={formError} canStart={canStart}
        update={updateDraft} save={() => void save()} discard={discard} close={() => setQuickOpen(false)}
        start={(trial) => trial ? void start(true) : guard((saved) => start(false, saved))} openFull={openFull} />
    </div> : <div key="editor" className={styles.viewFill}><RailSplit railKey={AGENTS_RAIL}>
      {/* The list's scrollbar passes under the width handle on the divider. */}
      <SecondaryRail railKey={AGENTS_RAIL} as="aside" id="agents-rail" label={text("Agent list", "Agent 列表")} data-scrollbar-layer="content">
        <RailSearch aria-label={text("Search Agents", "搜索 Agents")} value={search} onChange={setSearch} placeholder={text("Find an Agent…", "查找 Agent…")} />
        {/* The same two groups as the cards, Chat then Always-on; a group the search empties is not shown. */}
        {([[text("Chat", "对话"), visible.filter((agent) => !agent.always_on.enabled)], [text("Always-on", "常驻"), visible.filter((agent) => agent.always_on.enabled)]] as [string, Agent[]][]).map(([title, rows]) => rows.length ? <Fragment key={title}>
          <RailGroupLabel>{title}</RailGroupLabel>
          {rows.map((agent) => <div key={agent.id} className={`${styles.morphSlot} relative flex min-w-0`}>
            {/* The card's frame lands here, then fades into the plain row. */}
            {settingsFrame ? <motion.span aria-hidden className={styles.rowFrame} layoutId={agentFrame(agent.id)} transition={SETTINGS_FRAME_MOTION} style={{ borderRadius: 8 }} initial={{ opacity: 1 }} animate={{ opacity: 0, transition: { duration: 0.2, delay: 0.3 } }} /> : null}
            <motion.div {...(settingsFrame ? MORPH_CONTENT : {})}><RailItem icon={<AgentAvatar agent={agent} size={16} />} label={agent.name || agent.id} description={agent.description || text("General conversations and tasks", "通用对话与任务")} badge={agent.default ? <RailBadge>{text("Default", "默认")}</RailBadge> : undefined} active={agent.id === draft.id} onClick={() => { if (agent.id !== draft.id) guard(() => applyAgent(agent, true)); }} /></motion.div>
          </div>)}
        </Fragment> : null)}
        {!visible.length ? <ManageEmptyState compact icon={<SearchIcon size={20} />} title={text("No matching Agents", "没有匹配的 Agent")} /> : null}
      </SecondaryRail>
      <SettingsWidthHandle expanded text={text} onToggle={showGrid} className={styles.editorHandle} />
      <motion.main layoutId={settingsFrame} transition={SETTINGS_FRAME_MOTION} className={styles.editor}
        onKeyDown={(event) => { if (event.key === "Escape" && !event.defaultPrevented && event.currentTarget.contains(event.target as Node)) showGrid(); }}>
      <motion.div layout={settingsFrame ? true : undefined} transition={SETTINGS_FRAME_MOTION} initial={settingsFrame ? { opacity: 0 } : false} animate={{ opacity: 1 }} className={`${settingsStyles.page} ${styles.editorPage}`}>
        <div className={styles.agentPageHeader}><div className={styles.agentHeaderRow}><span className="grid size-[40px] flex-none place-items-center max-[680px]:size-[28px]"><AgentAvatar agent={draft} size={40} /></span><div className={styles.agentHeading}><h2 className={settingsStyles.pageTitle}>{activeName}</h2><p className={settingsStyles.pageMeta}>{draft.id}{draft.default ? ` · ${text("Default Agent", "默认 Agent")}` : ""}</p></div>
        <div className={`${styles.conversationActions} flex items-center gap-[6px] flex-none min-w-0`}><AgentButton variant="elevated" icon={MessageCircleIcon} disabled={busy || !canStart || Boolean(formError)} aria-label={text("Try in new conversation", "在新对话试运行")} title={text("Start a new conversation with the draft; memory is read only or off.", "使用草稿开始新对话；记忆只读或关闭。")} onClick={() => void start(true)}>{text("Try draft", "试运行草稿")}</AgentButton><AgentButton variant="default" icon={MessageCircleIcon} disabled={busy} onClick={() => guard((saved) => start(false, saved))}>{text("New conversation", "新对话")}</AgentButton>
          <DropdownMenu key={draft.id}>
            <HoverTip label={text("Agent actions", "Agent 操作")}><DropdownMenuTrigger asChild><Button ref={menuTrigger} variant="ghost" size="icon-xs" className={styles.moreMenu} aria-label={text("Agent actions", "Agent 操作")}><SettingsIcon size={16} aria-hidden /></Button></DropdownMenuTrigger></HoverTip>
            <DropdownMenuContent align="end" sideOffset={4} className={cn(MENU_PANEL, "min-w-[190px] text-text-primary")}>
              <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} disabled={draft.default || busy} onSelect={() => guard(makeDefault)}><MenuRow glyph="check-circle" label={text("Set as default", "设为默认")} /></DropdownMenuItem>
              <DropdownMenuItem className={cn(itemCls(false), MENU_ITEM_STATES)} disabled={busy} onSelect={() => openDialog("duplicate")}><MenuRow glyph="copy" label={text("Duplicate Agent", "复制 Agent")} /></DropdownMenuItem>
              <DropdownMenuSeparator className={MENU_SEPARATOR} />
              <DropdownMenuItem className={cn(itemCls(false, true), MENU_ITEM_STATES)} disabled={draft.default || busy} onSelect={() => openDialog("delete")}><MenuRow glyph="trash-bin-trash" label={text("Delete Agent", "删除 Agent")} danger /></DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu></div></div>
          <AgentChips draft={draft} catalog={models} text={text} onTab={setTab} />
        </div>
        <Tabs orientation="horizontal" className={styles.configTabs} value={tab} onValueChange={(value) => setTab(value as TabId)}><div className={styles.configTabsBar}><TabsList variant="line" aria-label={text("Agent configuration", "Agent 配置")}>{TABS.map((item) => <TabsTrigger value={item.id} key={item.id}>{text(item.en, item.zh)}</TabsTrigger>)}</TabsList></div>
          <div className={`${settingsStyles.pageBody} ${styles.editorBody}`}><fieldset className={styles.editorFields} disabled={busy}>
            <TabsContent className={styles.tabPanel} value="overview"><OverviewPanel draft={draft} update={updateDraft} text={text} catalog={models} dirty={dirty} /></TabsContent>
            {(["programs", "skills", "mcp"] as const).map((kind) => <TabsContent className={styles.tabPanel} value={kind} key={kind}><CapabilityPanel kind={kind} draft={draft} update={updateDraft} text={text} /></TabsContent>)}
            <TabsContent className={styles.tabPanel} value="memory"><MemoryPanel draft={draft} update={updateDraft} text={text} /></TabsContent>
            <TabsContent className={styles.tabPanel} value="context"><ContextPanel draft={draft} update={updateDraft} text={text} /></TabsContent>
            <TabsContent className={styles.tabPanel} value="always_on"><AlwaysOnPanel draft={draft} update={updateDraft} text={text} /></TabsContent>
            <TabsContent className={styles.tabPanel} value="advanced"><AdvancedPanel draft={draft} update={updateDraft} text={text} /></TabsContent>
          </fieldset></div>
        </Tabs>
        <div className={styles.saveBar}>{formError ? <span role="alert" className={styles.inlineError}>{formError}</span> : <span>{dirty ? text("Changes apply after saving.", "保存后生效。") : text("All changes saved", "修改已保存")}</span>}<AgentButton variant="ghost" disabled={!dirty || busy} onClick={discard}>{text("Discard changes", "放弃修改")}</AgentButton><AgentButton icon={CheckIcon} disabled={!dirty || busy || Boolean(formError)} onClick={() => void save()}>{busy ? text("Working…", "处理中…") : text("Save changes", "保存修改")}</AgentButton></div>
      </motion.div></motion.main>
    </RailSplit></div>}
    <Dialog open={pending !== null} onOpenChange={(open) => { if (!open && !busy) setPending(null); }}><DialogContent onCloseAutoFocus={restoreDialogFocus}><DialogHeader><DialogTitle>{text("Unsaved changes", "未保存的修改")}</DialogTitle><DialogDescription>{text("Save or discard your changes before continuing.", "继续前保存或放弃修改。")}</DialogDescription></DialogHeader><DialogFooter><AgentButton variant="elevated" disabled={busy} onClick={() => setPending(null)}>{text("Cancel", "取消")}</AgentButton><AgentButton variant="elevated" disabled={busy} onClick={() => void resolveLeave("discard")}>{text("Discard", "放弃")}</AgentButton><AgentButton disabled={busy || Boolean(formError)} onClick={() => void resolveLeave("save")}>{text("Save and continue", "保存并继续")}</AgentButton></DialogFooter></DialogContent></Dialog>
    <Dialog open={conflict !== null} onOpenChange={(open) => { if (!open) setConflict(null); }}><DialogContent className={styles.conflictDialog} onCloseAutoFocus={restoreDialogFocus}><DialogHeader><DialogTitle>{text("Configuration changed on the server", "服务端配置已更新")}</DialogTitle><DialogDescription>{text("Your draft has been kept. Compare the values before replacing either version.", "你的草稿已保留。比较字段后明确选择保留哪份配置。")}</DialogDescription></DialogHeader>
      {conflict && draft ? <div className={`${styles.conflictBody} max-h-[52vh] overflow-auto`}><div className={styles.conflictLabels}><strong>{text("Your draft", "你的草稿")}</strong><strong>{text("Server configuration", "服务端配置")}</strong></div>{Object.entries(configuration(draft)).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(configuration(conflict)[key as keyof ReturnType<typeof configuration>])).map(([key, value]) => <section key={key}><h4>{key}</h4><div className={styles.conflictValues}><pre>{typeof value === "string" ? value || "—" : JSON.stringify(value, null, 2)}</pre><pre>{typeof configuration(conflict)[key as keyof ReturnType<typeof configuration>] === "string" ? String(configuration(conflict)[key as keyof ReturnType<typeof configuration>]) || "—" : JSON.stringify(configuration(conflict)[key as keyof ReturnType<typeof configuration>], null, 2)}</pre></div></section>)}</div> : null}
      <DialogFooter><AgentButton variant="elevated" onClick={() => setConflict(null)}>{text("Cancel", "取消")}</AgentButton><AgentButton variant="elevated" onClick={() => { if (conflict) { applyAgent(conflict); setTab(tab); } }}>{text("Load server version", "加载服务端版本")}</AgentButton><AgentButton onClick={keepDraft}>{text("Keep my draft", "保留我的草稿")}</AgentButton></DialogFooter>
    </DialogContent></Dialog>
    <Dialog open={dialog === "create" || dialog === "duplicate"} onOpenChange={(open) => { if (!open && !busy) setDialog(null); }}><DialogContent onCloseAutoFocus={restoreDialogFocus}><form className={styles.dialogForm} onSubmit={(event) => { event.preventDefault(); void submitName(); }}><DialogHeader><DialogTitle>{dialog === "duplicate" ? text("Duplicate Agent", "复制 Agent") : text("New Agent", "新建 Agent")}</DialogTitle><DialogDescription>{dialog === "duplicate" ? text("Copy saved configuration. Conversations and memory records are not copied.", "复制已保存配置，不复制对话和记忆记录。") : text("Give your Agent a name and choose its model.", "为 Agent 命名并选择模型。")}</DialogDescription></DialogHeader>{dialog === "create" ? <fieldset className={styles.creationFields} disabled={busy}>
        <ModelPicker draft={newModel} update={(patch) => setNewModel((current) => ({ ...current, ...patch }))} catalog={models} text={text} idPrefix="new-agent" />
      </fieldset> : null}<label className={styles.dialogField}>{text("Agent name", "Agent 名称")}<Input autoFocus value={newName} maxLength={80} disabled={busy} onChange={(event) => setNewName(event.target.value)} /></label>{dialogError ? <p className={styles.dialogError} role="alert">{dialogError}</p> : null}<DialogFooter><AgentButton type="button" variant="elevated" disabled={busy} onClick={() => setDialog(null)}>{text("Cancel", "取消")}</AgentButton><AgentButton type="submit" disabled={busy || !newName.trim() || (dialog === "create" && creationEffortInvalid)}>{dialog === "duplicate" ? text("Duplicate", "复制") : text("Create", "创建")}</AgentButton></DialogFooter></form></DialogContent></Dialog>
    <Dialog open={dialog === "delete"} onOpenChange={(open) => { if (!open && !busy) setDialog(null); }}><DialogContent onCloseAutoFocus={restoreDialogFocus}><DialogHeader><DialogTitle>{text("Delete Agent", "删除 Agent")}</DialogTitle><DialogDescription>{text("This removes the Agent configuration and sessions. Shared Programs, Skills and MCP services are kept.", "这会删除此 Agent 的配置和会话。共享的程序、技能与 MCP 服务保留。")}</DialogDescription></DialogHeader><label className={styles.dialogField}>{text(`Type ${baseline?.id || ""} to confirm`, `输入 ${baseline?.id || ""} 以确认`)}<Input value={deleteId} disabled={busy} onChange={(event) => setDeleteId(event.target.value)} /></label>{dialogError ? <p className={styles.dialogError} role="alert">{dialogError}</p> : null}<DialogFooter><AgentButton variant="elevated" disabled={busy} onClick={() => setDialog(null)}>{text("Cancel", "取消")}</AgentButton><AgentButton variant="destructive" disabled={busy || deleteId !== baseline?.id} onClick={() => void remove()}>{text("Delete Agent", "删除 Agent")}</AgentButton></DialogFooter></DialogContent></Dialog>
  </div></div>;
}
