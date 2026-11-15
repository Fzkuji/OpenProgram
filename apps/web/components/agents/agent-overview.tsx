"use client";

// Agents → the card grid that opens the page, the quick settings panel a
// card opens beside it, and the summary chips over the full editor. All
// three read the same draft as the full editor, so an edit made in one is
// still there in the other.

import { useEffect, useRef, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { BoxIcon, BrainIcon, ChevronRightIcon, ClockIcon, FlameIcon, MessageCircleIcon, PlugZapIcon, SearchIcon, SparklesIcon, XIcon } from "@/components/animated-icons";
import { Button } from "@/components/ui/button";
import { ManageEmptyState } from "@/components/ui/manage-page";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { HoverTip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { AgentAvatar } from "./agent-avatar";
import { AgentButton } from "./agent-controls";
import { ModelPicker, selectedModel, type ModelCatalog } from "./model-picker";
import { policyMode, type Agent, type TabId, type Text } from "./agent-types";
import styles from "./agents-page.module.css";

type Capability = "programs" | "skills" | "mcp";

/** layoutId the quick panel shares with the full editor's frame. */
export const AGENT_SETTINGS_FRAME = "agent-settings-frame";
export const SETTINGS_FRAME_MOTION = { duration: 0.3, ease: [0.32, 0.72, 0, 1] } as const;
/** layoutId an Agent's card shares with its row in the editor's list. */
export const agentFrame = (id: string) => `agent-frame-${id}`;
/** Contents arrive once their frame has mostly settled. The timing sits in
 *  `animate`, so a `transition` prop beside it still times layout moves. */
export const MORPH_CONTENT = { initial: { opacity: 0 }, animate: { opacity: 1, transition: { duration: 0.16, delay: 0.14 } } } as const;

/** The handle on the line between the Agents and their settings. It
 *  widens the settings (the cards fold into a list beside them) or narrows
 *  them back; the chevron points the way the line will move. */
export function SettingsWidthHandle({ expanded, text, onToggle, className }: { expanded: boolean; text: Text; onToggle: () => void; className?: string }) {
  const label = expanded ? text("Collapse settings", "收起设置") : text("Expand settings", "展开设置");
  return <motion.div className={cn(styles.widthHandle, className)} initial={{ opacity: 0 }} animate={{ opacity: 1, transition: { duration: 0.16, delay: 0.24 } }} exit={{ opacity: 0, transition: { duration: 0.1 } }}>
    <HoverTip label={label}><Button type="button" variant="elevated" className={styles.widthHandleButton} aria-label={label} onClick={onToggle}>
      <ChevronRightIcon size={14} aria-hidden className={expanded ? undefined : "-scale-x-100"} />
    </Button></HoverTip>
  </motion.div>;
}

/** "All programs", "3 skills", "No MCP": what a capability tab is set to. */
export function capabilityLabel(kind: Capability, agent: Agent, text: Text) {
  if (kind === "programs") {
    const tools = agent.tools;
    if (tools.mode === "automatic") return text("All programs", "全部程序");
    if (tools.mode === "none") return text("No programs", "不使用程序");
    if (tools.preset) return tools.preset;
    const count = tools.allowed?.length ?? 0;
    return text(`${count} ${count === 1 ? "program" : "programs"}`, `${count} 个程序`);
  }
  const policy = agent[kind];
  const mode = policyMode(policy);
  const [all, none, one, many, zh] = kind === "skills"
    ? [text("All skills", "全部技能"), text("No skills", "不使用技能"), "skill", "skills", "个技能"]
    : [text("All MCP", "全部 MCP"), text("No MCP", "不使用 MCP"), "MCP server", "MCP servers", "个 MCP 服务"];
  if (mode === "all") return all;
  if (mode === "none") return none;
  const count = policy.allowed.length + (policy.categories?.length ?? 0);
  return text(`${count} ${count === 1 ? one : many}`, `${count} ${zh}`);
}

function modelLabel(agent: Agent, catalog: ModelCatalog, text: Text) {
  if (!agent.model.id && !agent.model.provider) return text("Last used model", "上次使用的模型");
  return selectedModel(agent, catalog)?.name || agent.model.id;
}

function effortLabel(agent: Agent, text: Text) {
  return agent.thinking_effort || text("Default effort", "默认强度");
}

/** The chip row under the editor's header; each chip opens its tab. */
export function AgentChips({ draft, catalog, text, onTab }: { draft: Agent; catalog: ModelCatalog; text: Text; onTab: (tab: TabId) => void }) {
  const alwaysOn = draft.always_on.enabled;
  return <div className="flex flex-wrap gap-[6px]" role="group" aria-label={text("Agent summary", "Agent 概览")}>
    <AgentButton variant="elevated" icon={BrainIcon} onClick={() => onTab("overview")}>{modelLabel(draft, catalog, text)}</AgentButton>
    <AgentButton variant="elevated" icon={FlameIcon} onClick={() => onTab("overview")}>{effortLabel(draft, text)}</AgentButton>
    <AgentButton variant="elevated" icon={BoxIcon} onClick={() => onTab("programs")}>{capabilityLabel("programs", draft, text)}</AgentButton>
    <AgentButton variant="elevated" icon={SparklesIcon} onClick={() => onTab("skills")}>{capabilityLabel("skills", draft, text)}</AgentButton>
    <AgentButton variant="elevated" icon={PlugZapIcon} onClick={() => onTab("mcp")}>{capabilityLabel("mcp", draft, text)}</AgentButton>
    <AgentButton variant="elevated" icon={ClockIcon} className={alwaysOn ? undefined : "text-text-muted"} onClick={() => onTab("always_on")}>{alwaysOn ? text("Always-on", "常驻") : text("Always-on off", "常驻已关闭")}</AgentButton>
  </div>;
}

const GRID = "grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-[12px]";

/** The always-on cards' grid: one field of light the cards are windows
 *  onto (the sheen layers in agents-page.module.css). Each card's frame
 *  learns its place in the grid, measured again whenever the grid
 *  resizes, and every card's drift is set to run from the document's
 *  start, so a card that appears later joins the others in step. */
function SheenField({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const grid = ref.current;
    if (!grid) return;
    const place = () => {
      for (const slot of grid.children) {
        if (!(slot instanceof HTMLElement)) continue;
        const frame = slot.querySelector<HTMLElement>(`.${styles.cardFrameLive}`);
        if (!frame) continue;
        frame.style.setProperty("--agent-sheen-x", `${slot.offsetLeft}px`);
        frame.style.setProperty("--agent-sheen-y", `${slot.offsetTop}px`);
        for (const layer of frame.querySelectorAll<HTMLElement>(`.${styles.sheen}`)) {
          for (const animation of layer.getAnimations()) animation.startTime = 0;
        }
      }
    };
    place();
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(place) : null;
    observer?.observe(grid);
    return () => observer?.disconnect();
  });
  return <div ref={ref} className={`${GRID} relative`}>{children}</div>;
}

function AgentCard({ agent, text, active, morph, onOpen }: { agent: Agent; text: Text; active: boolean; morph: boolean; onOpen: () => void }) {
  // The frame is drawn apart from the contents so it can shrink into the
  // editor's list row (and grow back) without squashing the text. A card
  // says what the user picks an Agent by: who it is, what it does, and
  // whether it is the default; an always-on card's frame is alive (the
  // surf in agents-page.module.css, two layers the card is a window
  // onto), which is how the group tells its kind without a word
  // on every card. The model, effort and programs are settings, and stay
  // in the quick settings panel and the editor.
  const live = agent.always_on.enabled;
  return <div className={`${styles.morphSlot} relative flex min-w-0`}>
    <motion.span aria-hidden className={cn(styles.cardFrame, live && styles.cardFrameLive, active && styles.cardFrameActive)} layoutId={morph ? agentFrame(agent.id) : undefined} transition={SETTINGS_FRAME_MOTION} style={{ borderRadius: 12 }}>
      {live ? <><i className={`${styles.sheen} ${styles.sheen0}`} /><i className={`${styles.sheen} ${styles.sheen1}`} /></> : null}
    </motion.span>
    {/* layout="position" moves the contents with their frame when the
        grid reflows, instead of jumping ahead of it. */}
    <motion.button type="button" className={styles.agentCard} aria-pressed={active} onClick={onOpen} layout={morph ? "position" : undefined} transition={SETTINGS_FRAME_MOTION} {...(morph ? MORPH_CONTENT : {})}>
    <span className="flex items-center gap-[10px] min-w-0">
      <span className="grid size-[40px] flex-none place-items-center"><AgentAvatar agent={agent} size={40} /></span>
      <span className={`${styles.cardName} min-w-0 flex-1 flex flex-col`}><strong>{agent.name || agent.id}</strong></span>
      {agent.default ? <span className={styles.cardTag}>{text("Default", "默认")}</span> : null}
    </span>
    <span className={styles.cardDescription}>{agent.description || text("General conversations and tasks", "通用对话与任务")}</span>
  </motion.button></div>;
}

type QuickProps = {
  draft: Agent; catalog: ModelCatalog; text: Text; dirty: boolean; busy: boolean; formError: string; canStart: boolean;
  update: (patch: Partial<Agent>) => void; save: () => void; discard: () => void; close: () => void;
  start: (trial: boolean) => void; openFull: (tab: TabId) => void;
};

/** The small panel a card opens: the settings people change most often. */
function QuickSettings({ draft, catalog, text, dirty, busy, formError, canStart, update, save, discard, close, start, openFull }: QuickProps) {
  return <>
    <div className="flex items-center gap-[10px] p-[16px_12px_12px_16px]">
      <span className="grid size-[32px] flex-none place-items-center"><AgentAvatar agent={draft} size={32} /></span>
      <span className={`${styles.cardName} min-w-0 flex-1 flex flex-col`}><strong>{draft.name || draft.id}</strong><small>{draft.id}{draft.default ? ` · ${text("Default", "默认")}` : ""}</small></span>
      <HoverTip label={text("Close", "关闭")}><AgentButton variant="ghost" size="icon" icon={XIcon} aria-label={text("Close", "关闭")} onClick={close} /></HoverTip>
    </div>
    <div className={styles.quickActions}>
      <AgentButton variant="elevated" icon={MessageCircleIcon} disabled={busy || !canStart || Boolean(formError)} title={text("Start a new conversation with the draft; memory is read only or off.", "使用草稿开始新对话；记忆只读或关闭。")} onClick={() => start(true)}>{text("Try draft", "试运行草稿")}</AgentButton>
      <AgentButton icon={MessageCircleIcon} disabled={busy} onClick={() => start(false)}>{text("New conversation", "新对话")}</AgentButton>
    </div>
    <fieldset className={styles.quickBody} disabled={busy}>
      <label className={styles.dialogField} htmlFor="quick-name">{text("Display name", "显示名称")}<Input id="quick-name" value={draft.name} maxLength={80} onChange={(event) => update({ name: event.target.value })} /></label>
      <label className={styles.dialogField} htmlFor="quick-description">{text("Description", "描述")}<Textarea id="quick-description" className={styles.descriptionField} rows={2} value={draft.description} maxLength={2000} onChange={(event) => update({ description: event.target.value })} placeholder={text("When to use this Agent", "说明何时使用此 Agent")} /></label>
      <div className={styles.quickModel}><ModelPicker draft={draft} update={update} text={text} catalog={catalog} announceEffortError={dirty} idPrefix="quick" /></div>
      <div className={styles.quickRow}>
        <label htmlFor="quick-always-on"><strong>{text("Always-on", "常驻")}</strong><small>{text("Conversations wake on a schedule while you are away.", "你离开时会话按计划唤醒。")}</small></label>
        <Switch id="quick-always-on" checked={draft.always_on.enabled} onCheckedChange={(enabled) => update({ always_on: { ...draft.always_on, enabled } })} />
      </div>
      <div className={styles.quickRow}>
        <span><strong>{text("Capabilities", "能力")}</strong><small>{(["programs", "skills", "mcp"] as const).map((kind) => capabilityLabel(kind, draft, text)).join(" · ")}</small></span>
        <AgentButton variant="ghost" onClick={() => openFull("programs")}>{text("Manage", "管理")}</AgentButton>
      </div>
    </fieldset>
    <div className={styles.quickFoot}>
      {formError ? <span role="alert" className={styles.inlineError}>{formError}</span> : <span>{dirty ? text("Unsaved changes", "有未保存的修改") : ""}</span>}
      <AgentButton variant="ghost" disabled={!dirty || busy} onClick={discard}>{text("Discard", "放弃")}</AgentButton>
      <AgentButton disabled={!dirty || busy || Boolean(formError)} onClick={save}>{busy ? text("Working…", "处理中…") : text("Save", "保存")}</AgentButton>
    </div>
  </>;
}

/** The page's opening state: every Agent as a card, quick settings beside. */
export function AgentGrid({ agents, search, quickOpen, onOpen, busy, ...quick }: Omit<QuickProps, "draft"> & {
  draft: Agent | null; agents: Agent[]; search: string;
  quickOpen: boolean; onOpen: (agent: Agent) => void;
}) {
  const reduce = useReducedMotion();
  const { draft, text } = quick;
  const visible = agents.filter((agent) => `${agent.name} ${agent.id} ${agent.description}`.toLowerCase().includes(search.trim().toLowerCase()));
  // The always-on switch tells the two kinds apart: the Agents you talk
  // to, and the ones whose conversations wake on their own. An Agent moves
  // between the groups with its switch; a new one (the header's New Agent)
  // starts among the chat ones.
  const chat = visible.filter((agent) => !agent.always_on.enabled);
  const alwaysOn = visible.filter((agent) => agent.always_on.enabled);
  const open = quickOpen && draft !== null;
  const card = (agent: Agent) => <AgentCard key={agent.id} agent={agent} text={text} active={open && agent.id === draft?.id} morph={!reduce} onOpen={() => onOpen(agent)} />;
  // A group heads with its name and one line on what it is, then fills
  // the width with its cards; a group with nothing to show is left out.
  const group = (title: string, about: string, rows: Agent[], live = false) => rows.length ? <section className={styles.gridGroup}>
    <h2>{title}<span>{about}</span></h2>
    {live ? <SheenField>{rows.map(card)}</SheenField> : <div className={GRID}>{rows.map(card)}</div>}
  </section> : null;
  return <div className={`${styles.gridView} relative flex min-h-0 min-w-0`}>
    {/* The grid's scrollbar passes under the width handle beside the quick panel. */}
    <div className="flex-1 min-w-0 overflow-auto" data-scrollbar-layer="content">
      <div className={`${styles.gridInner} p-[24px_24px_32px] flex flex-col gap-[28px]`}>
        {!visible.length ? <ManageEmptyState compact icon={<SearchIcon size={20} />} title={text("No matching Agents", "没有匹配的 Agent")} /> : null}
        {group(text("Chat", "对话"), text("The Agents you talk to", "你和它对话的 Agent"), chat)}
        {group(text("Always-on", "常驻"), text("Wake on their own while you are away", "你不在时自己醒来干活"), alwaysOn, true)}
      </div>
    </div>
    <AnimatePresence initial={false}>
      {open && draft ? <SettingsWidthHandle key="handle" expanded={false} text={text} onToggle={() => quick.openFull("overview")} className={styles.gridHandle} /> : null}
      {open && draft ? <motion.aside key="quick" className={styles.quickPanel} aria-label={text(`Quick settings for ${draft.name || draft.id}`, `${draft.name || draft.id} 快速设置`)}
        initial={reduce ? { opacity: 0 } : { opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={reduce ? { opacity: 0 } : { opacity: 0, x: 24 }}
        layoutId={reduce ? undefined : AGENT_SETTINGS_FRAME} transition={SETTINGS_FRAME_MOTION}
        onKeyDown={(event) => { if (event.key === "Escape") quick.close(); }}>
        {/* Counter-scaled so the contents keep their size while the frame resizes. */}
        <motion.div layout={reduce ? undefined : true} transition={SETTINGS_FRAME_MOTION} className="flex-1 min-h-0 flex flex-col"><QuickSettings {...quick} busy={busy} draft={draft} /></motion.div>
      </motion.aside> : null}
    </AnimatePresence>
  </div>;
}
