"use client";

// Agents → Always-on: the configuration that makes every conversation
// using this Agent wake on its own (when the agent schedules it, or at a
// fixed interval), which model and budget its check-ins use, and which
// kinds of report notify. It is part of the Agent draft and is
// saved with the rest. What a conversation did on its wakes, and Wake now,
// are in that conversation's Activity panel.

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/net/api";
import {
  ALL_WAKE_DAYS, ALWAYS_ON_CHANNELS_MAX, channelChoices, dailyBudgetText, dailyBudgetValid, fetchAlwaysOn, parseDailyBudget, QUIET_HOURS_DEFAULT,
  REPORT_KINDS, sameChannelTarget, toggleChannelTarget, wakeMinutesValid, WAKE_MINUTES_DEFAULT, WAKE_MINUTES_MAX, WAKE_MINUTES_MIN,
  type AlwaysOnAgentInfo, type AlwaysOnConfig, type AlwaysOnNotifyRule, type AlwaysOnReportKind,
} from "@/lib/agents/always-on";
import { reportKindHelp, reportKindLabel, targetLabel, weekdayNames } from "./always-on-labels";
import type { Agent, Text } from "./agent-types";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import styles from "./agents-page.module.css";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, FieldLabel } from "@/components/ui/field";
import { SimpleSelect } from "@/components/ui/select";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

type Props = { draft: Agent; update: (patch: Partial<Agent>) => void; text: Text };

export function AlwaysOnPanel({ draft, update, text }: Props) {
  const config = draft.always_on;
  const [info, setInfo] = useState<AlwaysOnAgentInfo | null>(null);
  function patch(value: Partial<AlwaysOnConfig>) { update({ always_on: { ...config, ...value } }); }
  function patchNotify(kind: AlwaysOnReportKind, rule: Partial<AlwaysOnNotifyRule>) {
    patch({ notify: { ...config.notify, [kind]: { ...config.notify[kind], ...rule } } });
  }
  const { data: models } = useQuery({ queryKey: ["models-enabled"], queryFn: api.listEnabledModels });
  // The budget is typed as text so a half-typed amount ("0.") stays in the box.
  const [budget, setBudget] = useState(config.daily_budget_usd === null ? "" : String(config.daily_budget_usd));
  useEffect(() => {
    setBudget(config.daily_budget_usd === null ? "" : String(config.daily_budget_usd));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset when another Agent or a saved revision loads
  }, [draft.id, draft.revision]);
  // Discard changes puts back the value without a new revision.
  useEffect(() => { setBudget((typed) => dailyBudgetText(typed, config.daily_budget_usd)); }, [config.daily_budget_usd]);
  const modelOptions = [
    { value: "", label: text("Same as the conversation", "与会话相同") },
    ...(config.check_in_model && !(models ?? []).some((model) => `${model.provider}/${model.id}` === config.check_in_model)
      ? [{ value: config.check_in_model, label: config.check_in_model }] : []),
    ...(models ?? []).map((model) => ({ value: `${model.provider}/${model.id}`, label: `${model.name} · ${model.providerLabel || model.provider}` })),
  ];

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    fetchAlwaysOn(draft.id, controller.signal)
      .then((data) => { if (active) setInfo(data); })
      .catch(() => { if (active) setInfo({ agent_id: draft.id, config, channel_targets: [] }); });
    return () => { active = false; controller.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the channel list follows the Agent, not the draft
  }, [draft.id, draft.revision]);

  const choices = channelChoices(config.channels, info?.channel_targets ?? []);
  return <>
    <div className={`${styles.panelIntro} mb-[18px]`}><h3>{text("Always-on", "常驻")}</h3><p>{text(
      "Keep conversations with this Agent working while you are away: each one wakes when there is something to look at, works on the standing goal, checks for news with read-only tools in the background, and only writes to you when it finds something. A conversation's Activity panel shows its wakes and reports.",
      "让使用此 Agent 的会话在你离开时持续工作：每个会话在有事要看时唤醒，推进常驻目标，在后台用只读工具巡检新情况，只有发现值得说的事才给你发消息。会话的运行记录面板会显示它的唤醒和报告。",
    )}</p></div>
    <label className={styles.memorySpace}><Checkbox checked={config.enabled} onCheckedChange={(checked) => patch({ enabled: (checked === true) })} /><span><strong>{text("Keep conversations with this Agent always on", "让使用此 Agent 的会话常驻")}</strong><small>{text("A conversation takes part once you use it after this is on; older ones stay quiet until you use them again, and archiving one stops it. Each works with this Agent's model, instructions, capabilities and memory, under its own permissions. It runs on this computer, in each conversation's folder and sandbox, while OpenProgram's worker is running. Turning this off stops the wakes and keeps the conversations, reports, memory and these settings.", "开启后你用过的会话会加入；更早的会话在你再次使用前保持安静，归档会话即停止。每个会话使用此 Agent 的模型、指令、能力与记忆，遵循会话自己的权限设置。它在这台电脑上运行，使用各会话的文件夹和沙箱，需要 OpenProgram 的 worker 在运行。关闭后不再唤醒，会话、报告、记忆和这些设置都会保留。")}</small></span></label>
    <fieldset className={styles.memoryFields} disabled={!config.enabled}>
      <label className={styles.dialogField}>{text("Standing goal", "常驻目标")}<Textarea className={styles.alwaysOnGoal} rows={3} maxLength={4000} value={config.goal} onChange={(event) => patch({ goal: event.target.value })} placeholder={text("For example: keep the docs site free of broken links and tell me when a fix needs review.", "例如：保持文档站没有死链，有需要我审阅的修复时告诉我。")} /><small>{text("Started as a Goal in each conversation. A changed goal starts after the current one ends. Leave empty to only check in.", "作为目标在每个会话中启动。修改后的目标会在当前目标结束后开始。留空则只做巡检。")}</small></label>
      <label className={styles.memorySpace}><Checkbox checked={config.wake_minutes !== null} onCheckedChange={(checked) => patch({ wake_minutes: checked === true ? (config.wake_minutes ?? WAKE_MINUTES_DEFAULT) : null })} /><span><strong>{text("Wake on a fixed interval", "按固定间隔唤醒")}</strong><small>{config.wake_minutes === null
        ? text("Off: the agent decides when to look again. It schedules its next check when it knows something is due, and otherwise looks 1 hour after you last wrote, then further apart, up to every 100 hours.", "关闭：由 Agent 决定何时再看。它知道什么时候有事要看时会自己安排下次巡检；否则在你上次发消息 1 小时后看一次，之后间隔逐渐变长，最长 100 小时。")
        : text("The conversation also wakes at this interval. The agent can still schedule an earlier look.", "会话还会按这个间隔唤醒。Agent 仍可以自己安排更早的巡检。")}</small></span></label>
      {config.wake_minutes !== null ? <div className={`${styles.formGrid} grid grid-cols-[repeat(2,minmax(0,1fr))] gap-[15px]`}><label>{text("Wake every (minutes)", "唤醒间隔（分钟）")}<Input type="number" min={WAKE_MINUTES_MIN} max={WAKE_MINUTES_MAX} step={5} value={config.wake_minutes} onChange={(event) => patch({ wake_minutes: Number(event.target.value) })} aria-invalid={!wakeMinutesValid(config.wake_minutes)} /><small>{text(`${WAKE_MINUTES_MIN} to ${WAKE_MINUTES_MAX} minutes.`, `${WAKE_MINUTES_MIN} 到 ${WAKE_MINUTES_MAX} 分钟。`)}</small></label></div> : null}
      <div className={`${styles.formGrid} grid grid-cols-[repeat(2,minmax(0,1fr))] gap-[15px]`}>
        <label>{text("Model for check-ins", "巡检模型")}<SimpleSelect aria-label={text("Model for check-ins", "巡检模型")} className="max-w-none" value={config.check_in_model} onValueChange={(value) => patch({ check_in_model: value })} options={modelOptions} /><small>{text("Background check-ins can use a cheaper model. Goals and your own turns keep the conversation's model.", "后台巡检可以用更便宜的模型。目标和你自己的对话仍用会话的模型。")}</small></label>
        <label>{text("Daily check-in budget (USD)", "每日巡检预算（美元）")}<Input type="number" inputMode="decimal" min={0} step={0.5} value={budget} placeholder={text("No limit", "不限")} onChange={(event) => { setBudget(event.target.value); patch({ daily_budget_usd: parseDailyBudget(event.target.value) }); }} aria-invalid={!dailyBudgetValid(config.daily_budget_usd)} /><small>{text("For this Agent's check-ins across its conversations, per day in local time. When it is used up, check-ins pause until the next day; Wake now still runs.", "此 Agent 所有会话的巡检合计，按本机时间每天计算。用完后巡检暂停到第二天；立即唤醒照常运行。")}</small></label>
      </div>
      <fieldset className={styles.memoryFields}><legend>{text("When it wakes", "何时唤醒")}</legend>
        <div className={styles.dialogField}>{text("Wake on", "唤醒的日子")}
          <ToggleGroup type="multiple" variant="elevated" size="sm" spacing={1} className="grid w-full max-w-[420px] grid-cols-7"
            aria-label={text("Days it wakes on", "唤醒的日子")}
            value={(config.wake_days ?? ALL_WAKE_DAYS).map(String)}
            onValueChange={(values: string[]) => { if (values.length) patch({ wake_days: values.map(Number).sort((a, b) => a - b) }); }}>
            {weekdayNames(text).map((name, day) => <ToggleGroupItem key={day} value={String(day)} className="min-w-0">{name}</ToggleGroupItem>)}
          </ToggleGroup>
          <small>{text("Scheduled wakes skip the other days. At least one day stays on.", "其他日子不做计划唤醒。至少保留一天。")}</small>
        </div>
        <label className={styles.memorySpace}><Checkbox checked={config.quiet_hours !== null} onCheckedChange={(checked) => patch({ quiet_hours: checked === true ? (config.quiet_hours ?? QUIET_HOURS_DEFAULT) : null })} /><span><strong>{text("Quiet hours", "安静时段")}</strong><small>{text("No scheduled wakes in this stretch of the day, in this computer's local time; the next wake moves to its end. Wake now and messages from your other conversations still run.", "每天这段时间（按本机时间）不做计划唤醒，下次唤醒顺延到时段结束。立即唤醒和来自你其他会话的消息照常处理。")}</small></span></label>
        {config.quiet_hours ? <div className={`${styles.formGrid} grid grid-cols-[repeat(2,minmax(0,1fr))] gap-[15px] max-w-[420px]`}>
          <label>{text("From", "开始")}<Input type="time" value={config.quiet_hours.start} onChange={(event) => patch({ quiet_hours: { ...config.quiet_hours!, start: event.target.value } })} aria-invalid={config.quiet_hours.start === config.quiet_hours.end} /></label>
          <label>{text("Until", "结束")}<Input type="time" value={config.quiet_hours.end} onChange={(event) => patch({ quiet_hours: { ...config.quiet_hours!, end: event.target.value } })} aria-invalid={config.quiet_hours.start === config.quiet_hours.end} /><small>{text("An end before the start runs past midnight.", "结束早于开始表示跨过午夜。")}</small></label>
        </div> : null}
      </fieldset>
      <label className={styles.memorySpace}><Checkbox checked={config.research} onCheckedChange={(checked) => patch({ research: (checked === true) })} /><span><strong>{text("Check in when there is no goal to work on", "没有目标时进行巡检")}</strong><small>{text("Reviews memory, open work and the conversation and looks for news with read-only tools. Reports only what is worth your attention.", "回顾记忆、未完成事项和会话，用只读工具查找新情况，只报告值得你关注的内容。")}</small></span></label>
      <RadioGroup className={`${styles.modeGrid} ${styles.modeGridTwo}`} aria-label={text("What a check-in may do", "巡检可以做什么")} value={config.autonomy} onValueChange={(mode) => patch({ autonomy: mode as typeof config.autonomy })}>{([
        ["report", text("Report findings", "只报告发现"), text("Proposes work in a report; you decide what to start.", "在报告中提出建议，由你决定是否开始。")],
        ["act", text("Start goals on its own", "可自行开始目标"), text("May turn a finding into a Goal, which then runs under the conversation's permissions.", "可把发现转为目标，按会话权限执行。")],
      ] as const).map(([mode, label, help]) => <label key={mode} className={config.autonomy === mode ? styles.modeActive : styles.modeCard}><RadioGroupItem value={mode} /><span><strong>{label}</strong><small>{help}</small></span></label>)}</RadioGroup>
      <fieldset className={styles.memoryFields}><legend>{text("Notify me", "通知我")}</legend>
        {REPORT_KINDS.map((kind) => <div className={`${styles.memorySpace} flex-wrap`} key={kind}>
          <span className="min-w-0 flex-1"><strong>{reportKindLabel(kind, text)}</strong><small>{reportKindHelp(kind, text)}</small></span>
          <Field orientation="horizontal" className="w-auto"><Checkbox id={`always-on-notify-${kind}-desktop`} checked={config.notify[kind].desktop} onCheckedChange={(checked) => patchNotify(kind, { desktop: checked === true })} /><FieldLabel htmlFor={`always-on-notify-${kind}-desktop`}>{text("Desktop notification", "桌面通知")}</FieldLabel></Field>
          <Field orientation="horizontal" className="w-auto"><Checkbox id={`always-on-notify-${kind}-channels`} checked={config.notify[kind].channels} onCheckedChange={(checked) => patchNotify(kind, { channels: checked === true })} /><FieldLabel htmlFor={`always-on-notify-${kind}-channels`}>{text("Channels", "渠道")}</FieldLabel></Field>
        </div>)}
        <p className={styles.note}>{text("Every report still appears in its conversation with an unread dot. Channels means the channel conversations picked below and the channel a conversation is bound to.", "每条报告仍会出现在它的会话里并显示未读标记。渠道指下面选中的渠道会话，以及会话绑定的渠道。")}</p>
      </fieldset>
      <fieldset className={styles.memoryFields}><legend>{text("Also send reports to", "同时把报告发到")}</legend>
        {choices.length ? choices.map((target) => {
          const picked = config.channels.some((item) => sameChannelTarget(item, target));
          return <label className={styles.memorySpace} key={`${target.channel}:${target.account_id}:${target.peer_id}`}><Checkbox checked={picked} disabled={!picked && config.channels.length >= ALWAYS_ON_CHANNELS_MAX} onCheckedChange={(checked) => patch({ channels: toggleChannelTarget(config.channels, target, (checked === true)) })} /><span><strong>{targetLabel(target, text)}</strong><small>{text(`Account ${target.account_id}`, `账号 ${target.account_id}`)}</small></span></label>;
        }) : <p className={styles.note}>{info ? text("No channel conversations yet. Bind this Agent to a channel contact or talk to it on a channel, then pick the conversation here.", "还没有渠道会话。把此 Agent 绑定到某个渠道联系人，或在渠道上和它聊过之后，就能在这里选择。") : text("Loading channel conversations…", "正在加载渠道会话…")}</p>}
        <p className={styles.note}>{text("Reports always appear in the App, in the conversation that made them. Picked channel conversations also get each report whose kind has Channels on under Notify me.", "报告总会出现在 App 中产生它的会话里；选中的渠道会话还会收到在“通知我”里勾选了渠道的那几类报告。")}</p>
      </fieldset>
    </fieldset>
    {draft.memory.mode !== "read_write" ? <p className={styles.note}>{text("To learn from your feedback over time, set Memory to Read and write.", "如需随反馈逐步改进，请在长期记忆中选择“读写”。")}</p> : null}
    <label className={styles.memorySpace}><Checkbox checked={draft.hold_outbound} onCheckedChange={(checked) => update({ hold_outbound: checked === true })} /><span><strong>{text("Hold what it sends for my approval", "发送前等我批准")}</strong><small>{text("Applies whether or not always-on is enabled: each email or message this Agent sends through a connected service waits for your approval, in every permission mode. A check-in cannot ask, so it reports the draft instead.", "无论是否开启常驻都生效：此 Agent 通过已连接服务发出的每封邮件或每条消息都要等你批准，所有权限模式下都如此。巡检时无法询问，它会在报告里给出草稿。")}</small></span></label>
  </>;
}
