"use client";

import { useEffect, useState } from "react";
import { FileTextIcon, CopyIcon } from "@/components/animated-icons";
import { jsonFetch } from "@/lib/net/fetch-client";
import { SimpleSelect } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { HoverTip } from "@/components/ui/tooltip";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-ui";
import { AvatarPicker, type AvatarConfig, type AvatarSource } from "@/components/avatar";
import { AGENT_AVATAR_UPLOAD_MAX_BYTES, agentAvatarConfig } from "@/lib/agents/agent-avatar";
import { AgentButton } from "./agent-controls";
import { Button } from "@/components/ui/button";
import { ModelPicker, type ModelCatalog } from "./model-picker";
import { delegationOn, type Agent, type MemorySpace, type Text } from "./agent-types";
import styles from "./agents-page.module.css";
import { Checkbox } from "@/components/ui/checkbox";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

type Props = { draft: Agent; update: (patch: Partial<Agent>) => void; text: Text };
const AGENT_AVATAR_SOURCES: readonly AvatarSource[] = ["bot", "dicebear", "upload"];
function Intro({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className={`${styles.panelIntro} mb-[18px]`}><h3>{title}</h3><p>{children}</p></div>;
}
export function OverviewPanel({ draft, update, text, catalog, dirty }: Props & { catalog: ModelCatalog; dirty: boolean }) {
  return <div className="flex flex-col gap-[22px]">
    <SettingsSection title={text("Identity", "身份")} description={text("How this Agent appears in the composer and the Agent list.", "此 Agent 在输入框和 Agent 列表中的显示方式。")}>
      <SettingsRow label={text("Display name", "显示名称")} htmlFor="agent-name"><Input id="agent-name" className="w-64" value={draft.name} maxLength={80} onChange={(event) => update({ name: event.target.value })} /></SettingsRow>
      <SettingsRow label={text("Agent ID", "Agent ID")} description={text("Used by channels and the CLI; it cannot change.", "供频道与命令行使用，不可修改。")} htmlFor="agent-id"><Input id="agent-id" className="w-64 font-mono" value={draft.id} disabled /></SettingsRow>
      <SettingsRow stacked label={text("Description", "描述")} htmlFor="agent-description"><Textarea id="agent-description" className={styles.descriptionField} rows={2} value={draft.description} maxLength={2000} onChange={(event) => update({ description: event.target.value })} placeholder={text("When to use this Agent", "说明何时使用此 Agent")} /></SettingsRow>
      <SettingsRow label={text("Other agents can use it", "其他 Agent 可以调用")} htmlFor="agent-delegation">
        <HoverTip label={text("Listed to other agents with its description.", "连同描述一起列给其他 Agent。")}><span className="inline-flex"><Switch id="agent-delegation" checked={delegationOn(draft)} onCheckedChange={(on) => update({ delegation: on ? "on" : "off" })} /></span></HoverTip>
      </SettingsRow>
      <SettingsRow stacked label={text("Avatar", "头像")} description={text("A character, a generated picture, or an image of your own.", "选一个角色、一张生成的图，或上传自己的图片。")}>
        <AvatarPicker value={agentAvatarConfig(draft)} onChange={(avatar: AvatarConfig) => update({ avatar })} name={draft.name || draft.id} seed={draft.id} sources={AGENT_AVATAR_SOURCES} uploadMaxBytes={AGENT_AVATAR_UPLOAD_MAX_BYTES} />
      </SettingsRow>
    </SettingsSection>
    <SettingsSection title={text("Model", "模型")} description={text("Used whenever a conversation does not choose its own model.", "对话没有自选模型时使用。")}>
      <ModelPicker draft={draft} update={update} text={text} catalog={catalog} announceEffortError={dirty} layout="rows" />
    </SettingsSection>
  </div>;
}
export function MemoryPanel({ draft, update, text }: Props) {
  const memory = draft.memory;
  function patch(value: Partial<Agent["memory"]>) { update({ memory: { ...memory, ...value } }); }
  const spaces: Array<[MemorySpace, string, string]> = [
    ["self", text("This Agent", "此 Agent"), text("Memory kept separately for this Agent.", "为此 Agent 单独保存的记忆。")],
    ["legacy_global", text("Shared legacy memory", "旧版共享记忆"), text("Existing profile-wide memory shared with other Agents.", "旧版在整个 profile 内共享的记忆。")],
  ];
  return <><Intro title={text("Long-term memory", "长期记忆")}>{text("Choose whether this Agent can read or write memory between conversations.", "选择此 Agent 是否在不同对话间读取或写入记忆。")}</Intro>
    <RadioGroup className={styles.modeGrid} value={memory.mode} onValueChange={(mode) => patch({ mode: mode as typeof memory.mode })}>{([
      ["off", text("Off", "关闭"), text("Do not read or write long-term memory.", "不读取或写入长期记忆。")],
      ["read_only", text("Read only", "只读"), text("Use existing memory without adding new records.", "使用已有记忆，不新增记录。")],
      ["read_write", text("Read and write", "读写"), text("Use existing memory and save new records.", "使用已有记忆并保存新记录。")],
    ] as const).map(([mode, label, help]) => <label key={mode} className={memory.mode === mode ? styles.modeActive : styles.modeCard}><RadioGroupItem value={mode} /><span><strong>{label}</strong><small>{help}</small></span></label>)}</RadioGroup>
    <p className={styles.note}>{text("Trial conversations use read-only memory at most. Turning memory off keeps existing records.", "试运行最多使用只读记忆。关闭记忆不会删除已有记录。")}</p>
    <fieldset className={styles.memoryFields} disabled={memory.mode === "off"}><legend>{text("Read from", "读取范围")}</legend>
      {spaces.map(([space, label, help]) => <label className={styles.memorySpace} key={space}><Checkbox checked={memory.read_spaces.includes(space)} onCheckedChange={(checked) => patch({ read_spaces: (checked === true) ? [...memory.read_spaces, space] : memory.read_spaces.filter((item) => item !== space) })} /><span><strong>{label}</strong><small>{help}</small></span></label>)}
      <label className={styles.dialogField}>{text("Write to", "写入位置")}<SimpleSelect label={text("Write to", "写入位置")} className="w-full" disabled={memory.mode !== "read_write"} value={memory.write_space} onValueChange={(value) => patch({ write_space: value })} options={spaces.map(([space, label]) => ({ value: space, label }))} /></label>
      <label className={styles.memorySpace}><Checkbox checked={memory.required} onCheckedChange={(checked) => patch({ required: (checked === true) })} /><span><strong>{text("Require memory to be available", "要求记忆可用")}</strong><small>{text("If memory is unavailable, stop before starting a conversation.", "记忆不可用时阻止启动对话。")}</small></span></label>
    </fieldset>
  </>;
}
export function ContextPanel({ draft, update, text }: Props) {
  return <><Intro title={text("Context", "上下文")}>{text("Control how channel conversations are separated and when an inactive conversation resets.", "控制频道对话的隔离方式，以及空闲对话的重置规则。")}</Intro>
    <p className={styles.note}>{text("New conversations and trial conversations start with a new history. These routing rules apply to channel conversations.", "新对话与试运行都使用新历史。以下路由规则用于频道对话。")}</p>
    <div className={`${styles.formGrid} grid grid-cols-[repeat(2,minmax(0,1fr))] gap-[15px]`}><label className={styles.fullField}>{text("Session scope", "会话范围")}<SimpleSelect label={text("Session scope", "会话范围")} className="w-full" value={draft.session_scope} onValueChange={(value) => update({ session_scope: value })} options={[
      { value: "per-account-channel-peer", label: text("Per account, channel and peer", "按账号、频道与联系人隔离") },
      { value: "per-channel-peer", label: text("Per channel and peer", "按频道与联系人隔离") },
      { value: "per-peer", label: text("Per peer", "按联系人隔离") },
      { value: "main", label: text("Shared main session", "共享主会话") },
    ]} /></label>
      <label>{text("Idle reset (minutes)", "空闲重置（分钟）")}<Input type="number" min={0} max={525600} value={draft.session_idle_minutes} onChange={(event) => update({ session_idle_minutes: Number(event.target.value) })} /><small>{text("0 disables idle reset.", "0 表示禁用空闲重置。")}</small></label>
      <label>{text("Daily reset", "每日重置")}<Input type="time" value={draft.session_daily_reset} onChange={(event) => update({ session_daily_reset: event.target.value })} /><small>{text("Empty disables daily reset.", "留空表示禁用。")}</small></label>
    </div>
  </>;
}
export function AdvancedPanel({ draft, update, text }: Props) {
  const [agentsMd, setAgentsMd] = useState<{ global: { path: string; exists: boolean }; working_dir: string; loaded: string[] } | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [copied, setCopied] = useState("");
  useEffect(() => {
    let active = true; const controller = new AbortController(); setAgentsMd(null); setError("");
    void jsonFetch<NonNullable<typeof agentsMd>>("/api/agents-md", { signal: controller.signal }).then((data) => { if (active) setAgentsMd(data); }).catch((cause) => { if (active && cause.name !== "AbortError") setError(String(cause.message || cause)); });
    return () => { active = false; controller.abort(); };
  }, [attempt]);
  const agentsMdFiles = agentsMd ? [
    { path: agentsMd.global.path, exists: agentsMd.global.exists, scope: text("Every project", "所有项目") },
    ...agentsMd.loaded.filter((path) => path !== agentsMd.global.path).map((path) => ({ path, exists: true, scope: path.slice(0, path.lastIndexOf("/") + 1) })),
  ] : [];
  async function copy(path: string) {
    try { await navigator.clipboard.writeText(path); setCopied(path); }
    catch { setError(text("Unable to copy this path", "无法复制路径")); }
  }
  return <><Intro title={text("Advanced", "高级")}>{text("Channel identity, optional instructions and AGENTS.md.", "频道身份、可选指令与 AGENTS.md。")}</Intro>
    <div className={`${styles.formGrid} mb-[15px] grid grid-cols-[repeat(2,minmax(0,1fr))] gap-[15px]`}><label>{text("Channel identity", "频道身份")}<Input value={draft.identity.name} maxLength={80} onChange={(event) => update({ identity: { ...draft.identity, name: event.target.value } })} /></label><label>{text("Mention patterns", "提及规则")}<Input value={draft.identity.mention_patterns.join(", ")} onChange={(event) => update({ identity: { ...draft.identity, mention_patterns: event.target.value.split(",").map((item) => item.trim()).filter(Boolean) } })} /><small>{text("Comma-separated patterns.", "使用逗号分隔。")}</small></label>
      <label htmlFor="agent-isolation">{text("When delegated", "被委派时")}<HoverTip label={text("Where it works when another agent starts it. Automatic: its own worktree for background work that edits files, otherwise the same folder.", "其他 Agent 启动它时在哪里工作。自动：后台且会改文件时用独立工作树，否则用同一文件夹。")}><span className="flex min-w-0"><SimpleSelect id="agent-isolation" label={text("When delegated", "被委派时")} className="w-full max-w-none" value={draft.isolation ?? ""} onValueChange={(isolation) => update({ isolation })} options={[
        { value: "", label: text("Automatic", "自动") },
        { value: "none", label: text("Same folder", "同一文件夹") },
        { value: "worktree", label: text("Own worktree", "独立工作树") },
      ]} /></span></HoverTip></label></div>
    <label className={styles.dialogField}>{text("System prompt", "系统指令")}<Textarea className={styles.instructionsField} rows={6} maxLength={100000} value={draft.system_prompt} onChange={(event) => update({ system_prompt: event.target.value })} /><small>{text("Optional. Leave empty to use instructions from the call and Context.", "可选。留空时使用调用处与 Context 提供的指令。")}</small></label>
    <section className={styles.workspaceSection}><h4>AGENTS.md</h4><p className={styles.note}>{text("Every Agent reads AGENTS.md: the global file, then each one from the project root down to the working directory. One in a subdirectory is read when the Agent opens a file there. Closer files win on a conflict.", "所有 Agent 都读 AGENTS.md：先全局文件，再从项目根到工作目录逐级读取；子目录里的在 Agent 打开那里的文件时读取。离得越近越优先。")}</p>{error ? <p role="alert" className={styles.inlineError}>{error} <Button type="button" variant="link" size="xs" onClick={() => setAttempt((value) => value + 1)}>{text("Retry", "重试")}</Button></p> : agentsMd ? <><p className={styles.workspacePath}>{text("Working directory: ", "工作目录：")}{agentsMd.working_dir}</p>{agentsMdFiles.map((file) => <div className={styles.workspaceFile} key={file.path}><span><FileTextIcon size={16} aria-hidden /></span><div><strong>{file.scope}</strong><small>{file.exists ? file.path : text(`Not created yet: ${file.path}`, `尚未创建：${file.path}`)}</small></div><AgentButton variant="ghost" size="sm" icon={CopyIcon} aria-label={text(`Copy ${file.path}`, `复制 ${file.path}`)} onClick={() => void copy(file.path)}>{copied === file.path ? text("Copied", "已复制") : text("Copy path", "复制路径")}</AgentButton></div>)}</> : <p role="status">{text("Loading AGENTS.md…", "正在加载 AGENTS.md…")}</p>}</section>
  </>;
}
