"use client";

/**
 * Activity: what the conversation runs and keeps doing. Its agents are on
 * the chart (agent-chart/): the main agent, one per branch the reader made
 * (branches the runtime or a schedule opened are none), and the sub-agents
 * they started, each with its glyph on one block. The glyph carries what
 * the panel knows of the agent: its name and what its sub-agents cost when
 * pointed at, a red count for what waits on the reader, a click to its page
 * (a sub-agent's: its conversation in the chat), and a way to stop the
 * sub-agents still going. Before the conversation has a run the main agent
 * is one row in the chart's place. Above the chart, each only when it has
 * something: the reports from every always-on conversation still waiting
 * for the reader (the pending inbox), each opening its conversation; the
 * messages sent to other conversations that are still unanswered; Programs
 * and commands, the background programs running now (one that ended leaves
 * the list, and a command the agent waits on is read in the chat);
 * then what the conversation does on its own: its open work and its
 * scheduled tasks, a task that runs a prompt as one row with its runs in
 * short (task-row.tsx). Always-on is a setting of the conversation's Agent,
 * not an agent of its own: the chart's first row says when it wakes next.
 * Every other row opens one page (activity-page.tsx). Each overview section
 * folds to its header, like the sidebar's, and stays folded in this browser.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "@/lib/i18n";
import { useSessionStore } from "@/lib/session-store";
import { useExecutionDebugger } from "@/lib/execution/use-execution-debugger";
import { useManagedProcesses } from "@/lib/execution/use-managed-processes";
import { type ActiveToolCall, type AgentCall, type ManagedProcess } from "@/lib/net/execution/process-client";
import { type ExecutionSnapshot } from "@/lib/execution/execution-debugger";
import { activityModel, agentBranches, pruneBranches, spawnedBy, startedAt, type ActivityIssue, type BranchRow } from "@/lib/execution/activity-model";
import { toggleActivitySection, useActivityView } from "@/lib/prefs/activity-view";
import { rosterCost, rosterLive, rosterNeedsReader, rosterStatus, rosterTotal, type RosterLook, type RosterStatus } from "@/lib/execution/agent-roster";
import { canExecuteAction, newCommandId, buildExecutionCommand } from "@/lib/execution/execution-debugger";
import { postExecutionCommand } from "@/lib/net/execution/execution-client";
import type { OutgoingSend } from "@/lib/net/execution/activity-roster-client";
import { navigate } from "@/lib/nav/navigate";
import { showToast } from "@/lib/format-utils/toast";
import { fmtUsd } from "@/components/settings/providers/status/model";
import { loadSubAgentTranscript } from "@/lib/chat/transcript/sub-agent-transcripts";
import { sendRuntimeCommand } from "@/lib/runtime-bridge/session/session-load";
import { SectionHeader } from "@/components/sidebar/sessions/section-header";
import { Button } from "@/components/ui/button";
import { SidebarNotice } from "../sidebar-notice";
import { useConversationBranches } from "../use-conversation-branches";
import { useSessionWorktrees } from "@/lib/worktrees/session-worktrees";
import { branchWorktree, changedFiles, isOpen } from "@/lib/worktrees/worktree-model";
import { isAgentTask, useConversationWork, type OpenWorkItem, type Routine } from "@/lib/chat/conversation-work";
import { AgentAvatarById, useConversationAgentShape } from "@/components/agents/agent-avatar";
import { goalStatusLabel, outcomeLabel, reportKindTag } from "@/components/agents/always-on-labels";
import { copyText } from "@/lib/clipboard";
import { openPendingItem, pendingInOrder, usePendingInbox, type PendingItem } from "@/lib/agents/pending-inbox";
import { clock, ProgressBar, progressLook } from "./program-progress";
import styles from "./running-panel.module.css";
import { ActivityPage } from "./pages/activity-page";
import { usePageSlide } from "./use-page-slide";
import type { ActivityPageTarget, ActivityPanelContext } from "./pages/activity-page-types";
import { Glyph, AgentGlyph, Row, type Tone } from "./running-rows";
import { RosterRow, rosterBadgeWords, usageTip } from "./agent-roster";
import { useAgentRoster } from "./use-agent-roster";
import { prefetchActivityRuns } from "./runs/use-activity-runs";
import { TaskRow } from "./task-row";
import { WorkChart, type AgentMarks, type WorkChartMemory, type WorkChartScope } from "./agent-chart/agent-chart";
import { useWorkRuns } from "./runs/use-work-runs";
import type { WorkTask } from "./agent-chart/agent-chart-model";
import { reportNameForRun } from "./pages/always-on-row";
import { useTaskStore, type TaskSegment } from "@/lib/chat/transcript/task-blocks";
import { LIVE, nextWakeOf, duration, when, executionTone, executionState, programTone, programLive, programTitle, commandLine, programStatus, programBarTone, routineSchedule, routineRunLabel } from "./running-format";

type View = { kind: "main" | "call" | "program" | "work" | "routine" | "task"; id: string };

const PAGE = 20;

/** What a level-1 row opens. */
function pageFor(view: View): ActivityPageTarget {
  switch (view.kind) {
    case "main": return { kind: "agent", scopeKey: view.id };
    case "program": return { kind: "program", processId: view.id };
    case "call": return { kind: "command", callId: view.id };
    case "routine": return { kind: "routine", taskId: view.id };
    case "task": return { kind: "task", taskId: view.id };
    case "work": return { kind: "work", workId: view.id };
  }
}

/** The branches the conversation's chart reads at most: each is a read of its runs. */
const CHART_BRANCHES = 6;
const MAIN_HEAD: readonly (string | null)[] = [null];
const NO_SEGMENTS: TaskSegment[] = [];

export function RunningPanel({ active, sessionId, resetSignal }: { active: boolean; sessionId: string | null; resetSignal?: number }) {
  const { text } = useTranslation();
  // One page at a time, opened from level 1 and tied to its conversation.
  const [opened, setOpened] = useState<{ sessionId: string | null; target: ActivityPageTarget } | null>(null);
  const page = opened && opened.sessionId === sessionId ? opened.target : null;
  // Each open run reads its own record, so the shared inspector stays off.
  const state = useExecutionDebugger(active, sessionId, undefined, false);
  const processes = useManagedProcesses(active, sessionId, page?.kind === "program" ? page.processId : null);
  const { work: ongoing, refresh: refreshOngoing } = useConversationWork(active, sessionId);
  // Reports from every always-on conversation still waiting for the user.
  const pendingInbox = usePendingInbox();
  const [copied, setCopied] = useState<string | null>(null);
  const conversationTitle = useSessionStore(s => (sessionId ? s.conversations[sessionId]?.title : undefined) || "");
  // A conversation whose turns left no execution record still has its main
  // agent: resting, its row reads the conversation's last activity.
  const conversationUpdatedAt = useSessionStore(s => (sessionId ? s.conversations[sessionId]?.updated_at : undefined) ?? null);
  // The main agent's rows wear the conversation Agent's character.
  const mainShape = useConversationAgentShape(sessionId);
  // Questions and approvals are answered in the chat's input box, which
  // shows each one with its choices; Activity says what is asked and goes there.
  const allDecisions = useSessionStore(s => s.pendingDecisions);
  const listView = useActivityView();
  const conversationBranches = useConversationBranches(sessionId);
  // A branch running in its own git worktree says how many files it changed there.
  const worktrees = useSessionWorktrees(sessionId);
  const selectedBranchId = useSessionStore(s => (sessionId ? s.composerSettingsBySession[sessionId]?.active_branch_id ?? null : null));
  const [programsShown, setProgramsShown] = useState(PAGE);
  // A program row, and the group with its header, open and close by height
  // as programs start and end, as a project's rows do in the sidebar.
  const reducedMotion = useReducedMotion();
  const reveal = {
    initial: { height: 0, opacity: 0, overflow: "hidden" },
    animate: { height: "auto", opacity: 1, transitionEnd: { overflow: "visible" } },
    exit: { height: 0, opacity: 0, overflow: "hidden" },
    transition: reducedMotion ? { duration: 0 } : { height: { duration: 0.24, ease: [0.2, 0.7, 0.2, 1] }, opacity: { duration: 0.16 } },
  } as const;
  // What only the server knows of each helper: stalled, queued, its cost.
  const roster = useAgentRoster(active, sessionId);
  // The conversation's tasks as their newest replies left them: the chart's rows.
  const taskSegments = useTaskStore(s => (sessionId ? s.segmentsBySession[sessionId] : undefined) ?? NO_SEGMENTS);
  const chartTasks = useMemo(() => {
    const newest = new Map<string, TaskSegment>();
    for (const segment of taskSegments) if (!segment.legacy && (newest.get(segment.taskId)?.updatedAt ?? -1) <= segment.updatedAt) newest.set(segment.taskId, segment);
    return new Map<string, WorkTask>([...newest].map(([id, segment]) => {
      const steps = segment.steps.filter(step => step.status !== "dropped");
      return [id, { title: segment.title, done: steps.filter(step => step.status === "completed").length, total: steps.length }];
    }));
  }, [taskSegments]);
  const chartBox = useRef<HTMLDivElement>(null);
  const chartMemory = useRef<WorkChartMemory | null>(null);
  const [stopping, setStopping] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  // Level 1 leaves the document while a page rests over it; its scroll and
  // opener come back with it. A page slides in over it and off again.
  const levelOneScroll = useRef<HTMLDivElement>(null);
  const levelOneReturn = useRef({ scrollTop: 0, opener: -1, focus: false });
  const slide = usePageSlide(() => setOpened(null));
  const { cut: cutSlide, leave: leaveSlide } = slide;
  const overviewShown = !page || slide.beneath;
  // The chart reads the branches worth a row: the checked-out one, then the
  // newest. Read here, not in the chart: the runs stay while a page rests
  // over the overview.
  const chartBranches = useMemo(() => {
    const reader = conversationBranches.filter(branch => !branch.runtimeStarted);
    if (reader.length < 2) return null;
    return [...reader].sort((a, b) => Number(b.active) - Number(a.active) || (b.updatedAt ?? 0) - (a.updatedAt ?? 0)).slice(0, CHART_BRANCHES);
  }, [conversationBranches]);
  const { runsByHead: chartRuns, loaded: chartRead } = useWorkRuns(sessionId ?? "", chartBranches ? chartBranches.map(branch => branch.head) : MAIN_HEAD, active && overviewShown);
  useEffect(() => { cutSlide(); setOpened(null); setProgramsShown(PAGE); chartMemory.current = null; }, [sessionId, cutSlide]);
  useLayoutEffect(() => {
    const element = levelOneScroll.current;
    if (overviewShown && element) element.scrollTop = levelOneReturn.current.scrollTop;
  }, [overviewShown]);
  useLayoutEffect(() => {
    const element = levelOneScroll.current;
    const saved = levelOneReturn.current;
    if (page || !element || !saved.focus) return;
    saved.focus = false;
    const buttons = element.querySelectorAll<HTMLElement>("button");
    (buttons[saved.opener] ?? buttons[0])?.focus({ preventScroll: true });
  }, [page]);
  // Clicking Activity in the rail while already on it returns to level 1.
  useEffect(() => {
    if (!resetSignal) return;
    levelOneReturn.current.focus = false;
    leaveSlide();
  }, [resetSignal, leaveSlide]);
  useEffect(() => { setActionError(null); }, [page]);
  // What was read draws, and a new read replaces it in place without a word;
  // a read that fails leaves it there and is tried again behind it.

  if (!sessionId) return <SidebarNotice>{text("Select or start a conversation to view what it runs.", "选择或开始一个会话，查看它运行的内容。")}</SidebarNotice>;

  // Until the executions are read every call and program would look unowned.
  const executions = state.fetchedAt ? state.executions : [];
  const calls = state.fetchedAt ? processes.calls || [] : [];
  const spawns = state.fetchedAt ? processes.spawns || [] : [];
  const model = activityModel(executions, processes.items, calls);
  const branches = agentBranches(executions);
  const now = Date.now() / 1000;
  const open = (next: View) => {
    const target = pageFor(next);
    const element = levelOneScroll.current;
    if (element) levelOneReturn.current = { scrollTop: element.scrollTop, opener: [...element.querySelectorAll("button")].indexOf(document.activeElement as HTMLButtonElement), focus: false };
    slide.enter();
    setOpened({ sessionId, target });
  };
  const execution = (id: string) => executions.find(item => item.execution_id === id) ?? null;
  const mainName = text("Main agent", "主代理");
  /** Executions a branch runs itself: the agent (null: the main
   *  conversation) and the executions under it that no deeper agent owns. */
  const ownedBy = (owner: string | null) => new Set(executions.filter(item => branches.ownerOf(item) === owner && !taskOf(item)).map(item => item.execution_id));
  /** The sub-agent an `agent` call started, when it is listed: the one its
   *  transcript row was paired with, else the job that began right after it. */
  const agentOf = (entry: ActiveToolCall) => {
    if (entry.name !== "agent") return null;
    const paired = entry.tool_call_id ? spawns.find(item => item.job_id && item.id.endsWith(`_t_${entry.tool_call_id}`)) : undefined;
    return (paired?.job_id ? execution(paired.job_id) : null) ?? spawnedBy(entry, model.childrenOf(entry.execution_id));
  };
  /** Everything an agent covers: itself and every execution under it. */
  const subtreeOf = (item: ExecutionSnapshot) => {
    const ids = new Set<string>();
    const walk = (entry: ExecutionSnapshot) => { if (ids.has(entry.execution_id)) return; ids.add(entry.execution_id); model.childrenOf(entry.execution_id).forEach(walk); };
    walk(item);
    return ids;
  };
  /** What waits on the reader: a run paused on a question or an approval.
   *  A call or run cut short by a restart is history, not a task. */
  const waits = model.issues.filter((issue): issue is Extract<ActivityIssue, { kind: "execution" }> =>
    issue.kind === "execution" && issue.execution.status === "paused");
  /** A run's state, read as Interrupted when the call that started a
   *  sub-agent was cut off before it finished. */
  const stateOf = (item: ExecutionSnapshot) => !["completed", "failed"].includes(item.status) && !LIVE.includes(item.status)
    && calls.some(entry => entry.status !== "running" && agentOf(entry)?.execution_id === item.execution_id)
    ? text("Interrupted", "已中断") : executionState(item, text);
  const issuesOf = (item: ExecutionSnapshot) => {
    const ids = subtreeOf(item);
    return waits.filter(issue => ids.has(issue.execution.execution_id));
  };
  const activeIn = (ids: Set<string>) => executions.some(entry => ids.has(entry.execution_id) && LIVE.includes(entry.status))
    || calls.some(entry => ids.has(entry.execution_id) && entry.status === "running")
    || processes.items.some(entry => entry.execution_id && ids.has(entry.execution_id) && programLive(entry));
  /** Calls that started no sub-agent, made by this branch (null: the main agent). */
  const failedSpawns = (owner: string | null) => spawns.filter(item => !item.job_id && (item.owner_job_id ?? null) === owner
    && (owner === null || executions.some(entry => entry.execution_id === owner)));

  // ---- the main agent, one per branch -------------------------------------
  // With one branch the main agent is the whole conversation. With more,
  // each branch the reader made is a main agent of its own, holding the
  // runs on that branch; calls and programs whose execution is not listed,
  // and calls that started no sub-agent, count as the checked-out branch's.
  // A branch the runtime opened is not the reader's: it gets no row, and
  // its runs count as the checked-out branch's.
  const memberships = new Map((state.branches || []).filter(entry => entry.session_id === sessionId)
    .map(entry => [entry.head_msg_id, new Set(entry.execution_ids)]));
  const rootOf = (item: ExecutionSnapshot): ExecutionSnapshot => {
    let top = item;
    for (const seen = new Set<string>(); ; ) {
      const parent = branches.parentOf(top);
      if (!parent || seen.has(parent)) return top;
      seen.add(parent);
      const next = execution(parent);
      if (!next) return top;
      top = next;
    }
  };
  /** The scheduled task a run belongs to: its top-level run's. Its runs are
   *  the task's, listed on its page, not the main agent's. */
  const taskOf = (item: ExecutionSnapshot): string | null => rootOf(item).scheduled_task_id ?? null;
  type Scope = { key: string; branchId?: string; head: string | null; name: string; active: boolean; color: string | null; updatedAt: number | null; members: Set<string> | null };
  const readerBranches = conversationBranches.filter(branch => !branch.runtimeStarted);
  const runtimeRuns = conversationBranches.filter(branch => branch.runtimeStarted).flatMap(branch => [...(memberships.get(branch.head) ?? [])]);
  const scopes: Scope[] = readerBranches.length > 1
    ? readerBranches.map(branch => ({ key: branch.branchId || branch.head, branchId: branch.branchId, head: branch.head, active: branch.active, color: branch.color, updatedAt: branch.updatedAt,
      name: branch.name || text("Unnamed branch", "未命名分支"), members: new Set([...(memberships.get(branch.head) ?? []), ...(branch.active ? runtimeRuns : [])]) }))
    : [{ key: "main", head: null, name: mainName, active: true, color: null, updatedAt: conversationUpdatedAt, members: null }];
  const chartScopes: WorkChartScope[] = chartBranches
    ? chartBranches.map(branch => ({ key: branch.branchId || branch.head, head: branch.head, name: branch.name || text("Unnamed branch", "未命名分支"), color: branch.color, current: branch.active }))
    : [{ key: "main", head: null, name: mainName, color: null, current: true }];
  // Before the conversation has a run there is nothing to chart: the main agent is one row in its place.
  const hasChart = chartRuns.some(runs => runs.length > 0);
  const inScope = (scope: Scope, item: ExecutionSnapshot) => !scope.members || scope.members.has(rootOf(item).execution_id);
  // A scheduled run's helper is the task's, unless it waits on the reader.
  const fullTree = pruneBranches(branches.tree<AgentCall>(owner => failedSpawns(owner).map(leaf => ({ at: leaf.at, leaf }))),
    row => row.kind === "leaf" || !taskOf(row.item) || issuesOf(row.item).length > 0);
  /** What one main agent holds: its runs, what it is doing, what waits on the reader, its sub-agents. */
  function scopeFacts(scope: Scope) {
    const ids = new Set([...ownedBy(null)].filter(id => inScope(scope, execution(id)!)));
    const runs = executions.filter(item => ids.has(item.execution_id) && !branches.parentOf(item)).sort((a, b) => startedAt(b) - startedAt(a));
    const unlisted = (id: string | null | undefined) => scope.active && (!id || !execution(id));
    const running = calls.filter(entry => entry.status === "running" && (ids.has(entry.execution_id) || unlisted(entry.execution_id)));
    const programs = processes.items.filter(entry => programLive(entry) && entry.execution_id && ids.has(entry.execution_id));
    const live = executions.some(entry => ids.has(entry.execution_id) && LIVE.includes(entry.status)) || running.length > 0 || programs.length > 0;
    const issues = waits.filter(issue => ids.has(issue.execution.execution_id));
    const tree = scope.members ? pruneBranches(fullTree, row => row.kind === "agent" ? inScope(scope, row.item) : scope.active) : fullTree;
    return { ids, latestRun: runs[0] ?? null, running, programs, live, issues, tree };
  }
  type ScopeFacts = ReturnType<typeof scopeFacts>;
  const scopeOf = (key: string) => scopes.find(scope => scope.key === key) ?? scopes.find(scope => scope.active) ?? scopes[0];
  /** A main agent's state. Resting, it is just the time of its last
   *  message (the branch's own time when no run of it is listed). */
  function scopeStatus(facts: ScopeFacts, scope?: Scope): { label: string; tone: Tone } {
    const { ids, latestRun, running, live, issues } = facts;
    if (live) {
      const step = running.at(-1);
      const since = executions.filter(entry => ids.has(entry.execution_id) && LIVE.includes(entry.status)).map(startedAt).sort((a, b) => a - b)[0]
        ?? step?.started_at ?? now;
      const doing = !step ? text("Running", "正在运行")
        : step.name === "agent" ? text("Waiting for a sub-agent", "等待子代理")
          : `${text("Running", "正在运行")} ${step.description || step.name}`;
      return { label: `${doing} · ${duration(now - since, text)}`, tone: "run" };
    }
    if (issues.length) return { label: issues.length === 1 ? waitingState(issues[0].execution) : text("Waiting for you", "等你处理"), tone: "warn" };
    if (!latestRun) return { label: scope?.updatedAt ? when(scope.updatedAt, text) : "", tone: "muted" };
    const at = when(latestRun.terminal_at ?? latestRun.updated_at ?? startedAt(latestRun), text);
    if (latestRun.status === "failed") return { label: `${text("Failed", "失败")} · ${at}`, tone: "err" };
    if (["completed", "reconciliation_required", "paused"].includes(latestRun.status)) return { label: at, tone: "muted" };
    return { label: `${executionState(latestRun, text)} · ${at}`, tone: "muted" };
  }

  /** The open question or approval an execution is waiting on, if the chat has it. */
  function decisionFor(executionId: string) {
    return allDecisions.find(entry => entry.sessionId === sessionId && entry.executionId === executionId);
  }
  /** Bring the chat's card for this decision into view, with its first choice focused. */
  function answerInChat(decisionId: string) {
    const card = [...document.querySelectorAll<HTMLElement>("[data-decision-output]")]
      .find(node => node.getAttribute("data-decision-output") === decisionId);
    if (!card) { useSessionStore.getState().focusComposer(); return; }
    card.scrollIntoView({ block: "nearest", behavior: "smooth" });
    card.querySelector<HTMLElement>("button:not([disabled]), input:not([disabled]), textarea:not([disabled])")?.focus({ preventScroll: true });
  }

  /** Check out a branch, the same as clicking it in the Branches panel. */
  function switchTo(head: string, branchId?: string) {
    sendRuntimeCommand({ action: "checkout_branch", session_id: sessionId, head_msg_id: head, branch_id: branchId });
    sendRuntimeCommand({ action: "load_session", session_id: sessionId });
  }

  /** Show a sub-agent's conversation in the chat, as its card's Switch does. */
  function openAgentConversation(jobId: string) {
    const job = execution(jobId);
    void (async () => {
      const target = await loadSubAgentTranscript(sessionId!, jobId, { jobId, headId: job?.display?.assistant_message_id }) || job?.display?.assistant_message_id;
      if (!target) return;
      sendRuntimeCommand({ action: "checkout_branch", session_id: sessionId, head_msg_id: target });
      sendRuntimeCommand({ action: "load_session", session_id: sessionId });
    })();
  }

  // ---- the roster under an agent ---------------------------------------------
  type RosterEntry = { row: BranchRow<AgentCall>; depth: number; status: RosterStatus };
  /** What the page sees of a helper right now; the server's facts refine it. */
  function jobLook(item: ExecutionSnapshot): RosterLook {
    const issues = issuesOf(item);
    if (issues.length) return issues.some(issue => decisionFor(issue.execution.execution_id)?.kind === "approval") ? "approval" : "input";
    if (activeIn(subtreeOf(item))) return "run";
    return executionTone(item) === "err" ? "err" : "rest";
  }
  const rosterLook = (row: BranchRow<AgentCall>): RosterLook => row.kind === "leaf" ? (row.leaf.failed ? "err" : "rest") : jobLook(row.item);
  const factsOf = (row: BranchRow<AgentCall>) => row.kind === "agent" ? roster.agents[row.item.execution_id] ?? null : null;
  /** What one agent's sub-agents add up to: the ones that can still be
   *  stopped, and what they cost together. */
  function rosterOf(facts: ScopeFacts) {
    const entries: RosterEntry[] = facts.tree.map(row => ({ row, depth: row.depth, status: rosterStatus(rosterLook(row), factsOf(row)) }));
    // The topmost helpers that can still be stopped: a stop reaches what they started.
    const stoppable: ExecutionSnapshot[] = [];
    let inside = Infinity;
    for (const entry of entries) {
      if (entry.depth <= inside) inside = Infinity;
      if (inside !== Infinity || entry.row.kind !== "agent") continue;
      if (rosterLive(entry.status) && canExecuteAction(entry.row.item, "cancel")) { stoppable.push(entry.row.item); inside = entry.depth; }
    }
    return {
      stoppable,
      // A helper's cost already holds the helpers under it; a scheduled
      // run's helper is in its task's cost.
      cost: rosterTotal(entries.filter(entry => entry.depth === 1 && !(entry.row.kind === "agent" && taskOf(entry.row.item))).map(entry => rosterCost(factsOf(entry.row)))),
    };
  }
  /** Stop every helper that is still going. */
  async function stopAll(items: ExecutionSnapshot[]) {
    if (!items.length || stopping) return;
    setStopping(true);
    showToast(text("Stop requested. Cancelling the current operation…", "已请求停止，正在取消当前操作…"));
    try {
      await Promise.allSettled(items.map(item => postExecutionCommand(buildExecutionCommand(item, "cancel", newCommandId()))));
    } finally { setStopping(false); }
  }

  // ---- rows ----------------------------------------------------------------
  /** An agent's status line: what waits on the reader first, then what runs. */
  function agentLook(item: ExecutionSnapshot): { label: string; tone: Tone; at: number } {
    const ids = subtreeOf(item);
    const issues = issuesOf(item);
    const live = activeIn(ids);
    const runningCall = calls.filter(entry => ids.has(entry.execution_id) && entry.status === "running").at(-1);
    if (issues.length) return {
      label: issues.length === 1 ? `${waitingState(issues[0].execution)} · ${when(issues[0].at, text)}` : text("Waiting for you", "等你处理"),
      tone: "warn", at: live ? now : startedAt(item),
    };
    if (live) return {
      label: `${runningCall ? `${text("Running", "正在运行")} ${runningCall.description || runningCall.name}` : text("Running", "正在运行")} · ${duration(now - startedAt(item), text)}`,
      tone: "run", at: now,
    };
    return { label: `${stateOf(item)} · ${when(startedAt(item), text)}`, tone: executionTone(item), at: startedAt(item) };
  }
  /** A paused or interrupted execution in the reader's words: what it waits on. */
  function waitingState(item: ExecutionSnapshot): string {
    const decision = item.status === "paused" ? decisionFor(item.execution_id) : undefined;
    if (decision) return decision.kind === "approval" ? text("Waiting for approval", "等你授权") : text("Waiting for your answer", "等你回答");
    return stateOf(item);
  }
  function programRow(item: ManagedProcess): ReactNode {
    const look = progressLook(item.progress);
    const active = programLive(item);
    // A running program always shows a bar (sweeping when the amount is
    // unknown); a finished one keeps its bar only if it reported progress.
    const bar = active || look?.fraction != null
      ? <span className="block m-[5px_0_2px]"><ProgressBar fraction={look?.fraction ?? null} tone={programBarTone(item, look)}
        note={active ? look?.eta && text(`${look.eta} left`, `剩 ${look.eta}`) : item.ended_at != null ? clock(item.ended_at - item.started_at) : null} /></span>
      : null;
    return <Row key={`program:${item.id}`} glyph={<Glyph name="code-square" tone="shell" large />} title={programTitle(item, text)}
      sub={commandLine(item)} status={programStatus(item, look, now, text)} tone={programTone(item)} extra={bar}
      onOpen={() => open({ kind: "program", id: item.id })} />;
  }
  // ---- what the conversation does on its own ---------------------------------
  const alwaysOn = ongoing?.always_on.enabled ? ongoing.always_on : null;
  const openItems = (ongoing?.open_work ?? []).filter(item => item.status === "open");
  const routines = ongoing?.routines ?? [];
  /** The always-on row: what it is doing, else when it wakes next. */
  function alwaysStatus(): { label: string; tone: Tone } {
    if (!alwaysOn) return { label: "", tone: "muted" };
    if (alwaysOn.running) return { label: text("Working now", "正在工作"), tone: "run" };
    const next = alwaysOn.next_wake_at
      ? alwaysOn.quiet_now
        ? text(`Quiet hours · next wake ${when(alwaysOn.next_wake_at, text)}`, `安静时段 · 下次唤醒 ${when(alwaysOn.next_wake_at, text)}`)
        : text(`Next wake ${when(alwaysOn.next_wake_at, text)}`, `下次唤醒 ${when(alwaysOn.next_wake_at, text)}`)
      : text("Joins on its next use", "下次使用后加入");
    const goal = alwaysOn.goal ? goalStatusLabel(alwaysOn.goal.status, text) : "";
    // A paused budget or an interrupted check-in is what the reader needs to see first.
    const held = alwaysOn.last_wake?.outcome === "budget" || alwaysOn.last_wake?.outcome === "interrupted"
      ? outcomeLabel(alwaysOn.last_wake.outcome, text) : "";
    return { label: [held || goal, next].filter(Boolean).join(" · "), tone: alwaysOn.last_wake?.outcome === "interrupted" ? "warn" : "muted" };
  }
  function pendingRow(item: PendingItem): ReactNode {
    return <Row key={`pending:${item.session_id}:${item.report_id}`}
      // A scheduled task's result wears the task's calendar, as on the Tasks page's Inbox.
      glyph={item.task_id
        ? <Glyph name="calendar" tone="plan" large />
        : <span className="grid size-[22px] flex-none place-items-center"><AgentAvatarById agentId={item.agent_id} size={20} /></span>}
      title={item.title} status={[reportKindTag(item.kind, text), item.task_id ? text("Scheduled task", "定时任务") : item.agent_name, when(item.at, text)].join(" · ")}
      tone={item.kind === "approval" ? "warn" : "muted"} onOpen={() => openPendingItem(item)} />;
  }
  function openWorkRow(item: OpenWorkItem): ReactNode {
    return <Row key={`work:${item.id}`} glyph={<Glyph name="checklist" tone="plan" large />} title={item.text}
      status={[item.status === "done" ? text("Done", "已完成") : text("Open", "未完成"), when(item.updated_at, text)].join(" · ")}
      tone={item.status === "done" ? "ok" : "muted"} onOpen={() => open({ kind: "work", id: item.id })} />;
  }
  /** A scheduled task: one that runs a prompt is one row with its runs in short. */
  function routineRow(task: Routine): ReactNode {
    if (isAgentTask(task)) return <TaskRow key={`task:${task.id}`} task={task} text={text} onOpen={() => open({ kind: "task", id: task.id })} />;
    return <Row key={`routine:${task.id}`} glyph={<Glyph name={task.type === "webhook" ? "link" : "calendar"} tone="plan" large />}
      title={task.title} sub={task.watch} tone={task.runs?.[0]?.status === "failed" ? "warn" : "muted"}
      status={[routineSchedule(task, text), task.runs?.[0] && routineRunLabel(task.runs[0], text)].filter(Boolean).join(" · ")}
      onOpen={() => open({ kind: "routine", id: task.id })} />;
  }
  async function act(run: () => Promise<unknown>, failed: string, key?: string) {
    setPending(key ?? "act"); setActionError(null);
    try { await run(); refreshOngoing(); return true; }
    catch (cause) { setActionError(`${failed} ${cause instanceof Error ? cause.message : ""}`.trim()); return false; }
    finally { setPending(null); }
  }
  async function copy(key: string, value: string) {
    if (await copyText(value)) { setCopied(key); setTimeout(() => setCopied(current => current === key ? null : current), 1500); }
  }

  // ---- the sub-agent tree ------------------------------------------------------
  const rowKey = (row: BranchRow<AgentCall>) => row.kind === "agent" ? row.item.execution_id : `spawn:${row.leaf.id}`;
  const looks = new Map(fullTree.map(row => [rowKey(row), row.kind === "agent" ? agentLook(row.item) : { tone: (row.leaf.failed ? "err" : "muted") as Tone, at: row.leaf.at }]));
  const lookOf = (row: BranchRow<AgentCall>) => looks.get(rowKey(row)) ?? { tone: "muted" as Tone, at: 0 };
  // ---- second-level pages -------------------------------------------------------
  const scopeHeadText: ActivityPanelContext["scopeHeadText"] = scope => {
    const { label, tone } = scopeStatus(scopeFacts(scope), scope);
    return {
      kind: scope.head ? (scope.active ? `${text("Branch", "分支")} · ${text("Current", "当前")}` : text("Branch", "分支")) : mainName,
      title: scope.head ? scope.name : conversationTitle || mainName,
      status: label, tone,
    };
  };
  const waitingFor: ActivityPanelContext["waitingFor"] = item => {
    const decision = item.status === "paused" ? decisionFor(item.execution_id) : undefined;
    if (decision) {
      const approval = decision.kind === "approval";
      return {
        answerLabel: approval ? text("Review in chat", "去聊天里授权") : text("Answer in chat", "去聊天里回答"),
        question: decision.prompt || decision.questions?.[0]?.prompt || "",
        choices: decision.options?.length ? decision.options : decision.questions?.[0]?.options ?? [],
        waitLabel: approval ? text("Waiting for approval", "等你授权") : text("Waiting for your answer", "等你回答"),
        approval,
      };
    }
    if (item.status === "paused" && item.reason_code === "wait_open") return { answerLabel: "", question: "", choices: [], waitLabel: text("Waiting for your answer", "等你回答") };
    return null;
  };
  const context: ActivityPanelContext = {
    sessionId, text, now, active,
    connected: state.connection.state === "connected",
    controller: state, processes, work: ongoing, refreshWork: refreshOngoing,
    scopes, scopeOf, scopeHeadText, waitingFor,
    answerInChat: item => {
      const decision = decisionFor(item.execution_id);
      if (decision) answerInChat(decision.id); else useSessionStore.getState().focusComposer();
    },
    switchTo: scope => { if (scope.head) switchTo(scope.head, scope.branchId); },
    openAgentConversation,
    alwaysStatus, act, pendingKey: pending, actionError, copy: (key, value) => void copy(key, value), copiedKey: copied,
    close: () => { levelOneReturn.current.focus = true; slide.leave(); },
    pull: slide.pull,
  };
  const pageLayer: ReactNode = page ? <div ref={slide.layer} className={styles.page}><ActivityPage key={JSON.stringify(page)} target={page} context={context} /></div> : null;

  // ---- overview ----------------------------------------------------------------
  // Messages live in the chat, and the agents are on the chart.
  // Programs and commands lists what they run in a shell.
  // The background programs going now, newest first. One that ended leaves
  // at once, failed or not, as in Claude Code and Codex: the agent has its
  // result, and the chat keeps every command. A command the agent waits on
  // is read in the chat and on the chart's message, not here.
  const work = processes.items.filter(programLive).sort((a, b) => b.started_at - a.started_at);
  /** One overview group: its header folds the rows under it. Until its
   *  data is read it stays folded and does not open. */
  const section = (key: string, name: ReactNode, rows: ReactNode, ready = true, actions?: ReactNode) => {
    const collapsed = !ready || listView.collapsed.includes(key);
    return <div className="group/sec">
      <SectionHeader name={name} className={styles.sectionHeader} collapsible collapsed={collapsed} actions={actions}
        onToggle={() => { if (ready) toggleActivitySection(key); }} />
      {!collapsed && rows}
    </div>;
  };
  // Every group, header included, appears only when it has something in it.
  const agentsRead = Boolean(state.fetchedAt);
  const programsReady = agentsRead && processes.loaded;
  const scheduledReady = ongoing != null;
  // ---- the agents on the chart ----------------------------------------------
  // What each main agent holds, worked out once for every glyph that asks.
  const scopeEntries = new Map(scopes.map(scope => { const facts = scopeFacts(scope); return [scope.key, { scope, facts, list: rosterOf(facts) }] as const; }));
  // A sub-agent waits when the page holds its question, or when the server says a running one does.
  const waitingOn = (facts: ScopeFacts) => facts.issues.length
    + facts.tree.filter(row => lookOf(row).tone === "warn" || rosterNeedsReader(rosterStatus(rosterLook(row), factsOf(row)))).length;
  const connected = state.connection.state === "connected";
  /** What a glyph on the chart says and does: a main agent's by its branch, a sub-agent's by its job. */
  const chartMarks: AgentMarks = agent => {
    if (agent.sub) {
      const job = agent.key ? execution(agent.key) : null;
      const facts = agent.key ? roster.agents[agent.key] ?? null : null;
      const cost = rosterCost(facts);
      const status = job ? rosterStatus(jobLook(job), facts) : "idle";
      return {
        tip: [
          agent.name, status !== "idle" ? rosterBadgeWords(status, text) : "",
          cost !== null ? fmtUsd(cost) : "", usageTip(facts, text),
        ].filter(Boolean).join(" · "),
        waiting: job ? Math.max(issuesOf(job).length, rosterNeedsReader(status) ? 1 : 0) : 0,
        onOpen: agent.key ? () => openAgentConversation(agent.key!) : undefined,
      };
    }
    const entry = agent.key ? scopeEntries.get(agent.key) : undefined;
    if (!entry) return { tip: agent.name, waiting: 0 };
    const { scope, facts, list } = entry;
    const tree = branchWorktree(worktrees.rows, scope.branchId ?? (scope.active ? selectedBranchId : null));
    const files = tree && isOpen(tree) ? changedFiles(tree) : null;
    const waiting = waitingOn(facts);
    return {
      tip: [
        scope.name, scope.head && scope.active ? text("Current", "当前") : "",
        waiting > 0 ? text(`${waiting} waiting for you`, `${waiting} 项等你处理`) : "",
        list.cost !== null ? text(`sub-agents ${fmtUsd(list.cost)}`, `子代理 ${fmtUsd(list.cost)}`) : "",
        files !== null ? text(`${files} ${files === 1 ? "file" : "files"} in worktree`, `worktree 中 ${files} 个文件`) : "",
      ].filter(Boolean).join(" · "),
      waiting,
      onOpen: () => open({ kind: "main", id: scope.key }),
      stop: list.stoppable.length ? { busy: stopping || !connected, run: () => void stopAll(list.stoppable) } : undefined,
    };
  };
  const stoppableScopes = [...scopeEntries.values()].filter(entry => entry.list.stoppable.length > 0).map(entry => entry.scope.key);
  const currentScope = scopes.find(scope => scope.active) ?? scopes[0];
  // Before its first run the main agent is one row where the chart will be:
  // its page is there from the start. Nothing is said while the runs are read.
  const mainRow = (): ReactNode => {
    const entry = scopeEntries.get(currentScope.key)!;
    const look = entry.facts.issues.length ? scopeStatus({ ...entry.facts, issues: [] }, currentScope) : scopeStatus(entry.facts, currentScope);
    const always = alwaysOn ? alwaysStatus() : null;
    const resting = look.tone === "muted";
    return <Row glyph={<AgentGlyph shape={mainShape} color={currentScope.color} />} title={currentScope.name} count={waitingOn(entry.facts)}
      status={resting ? (always ? `${text("Always-on", "常驻")} · ${always.label.charAt(0).toLowerCase()}${always.label.slice(1)}` : look.label || text("No runs yet", "还没有运行记录")) : look.label}
      tone={resting && always ? always.tone : look.tone === "warn" ? "muted" : look.tone}
      onOpen={() => open({ kind: "main", id: currentScope.key })}
      onIntent={() => prefetchActivityRuns(sessionId, { kind: "branch", head: currentScope.head }, true)} />;
  };
  // Messages are sent from the branch the reader is on.
  const outgoing: OutgoingSend[] = roster.outgoing;
  return <>{overviewShown ? <section ref={slide.under} className={styles.panel} aria-label={text("Conversation activity", "会话运行记录")} inert={Boolean(page)}>
    <div ref={levelOneScroll} className={`${styles.scroll} ${hasChart ? "max-h-[50%] flex-none" : "flex-1"} min-h-0 overflow-y-auto p-[0_8px_8px]`}>
      {pendingInbox.items.length > 0 && section("pending",
        text(`Waiting for you · ${pendingInbox.items.length}`, `等你处理 · ${pendingInbox.items.length}`),
        pendingInOrder(pendingInbox.items).map(pendingRow))}
      {!hasChart && chartRead ? mainRow() : null}
      {outgoing.length > 0 && section("sent", text("Sent messages", "发出的消息"), outgoing.map(send => <RosterRow key={`out:${send.job_id}`} outgoing name={send.to} indent={0} text={text}
        status={send.status === "waiting" ? "waiting" : rosterStatus("run", { agent_status: send.status, queued: false, usage: null })}
        onOpen={() => navigate(`/s/${send.to_session}`)} />))}
      <AnimatePresence initial={false}>{work.length > 0 && <motion.div key="programs" {...reveal}>{section("programs", text("Programs and commands", "程序和命令"), <>
        <AnimatePresence initial={false}>{work.slice(0, programsShown).map(entry => <motion.div key={entry.id} {...reveal}>{programRow(entry)}</motion.div>)}</AnimatePresence>
        {work.length > programsShown && <Button type="button" variant="link" size="xs" onClick={() => setProgramsShown(value => value + 50)}>
          {text(`Show ${Math.min(50, work.length - programsShown)} earlier`, `显示更早的 ${Math.min(50, work.length - programsShown)} 个`)}
        </Button>}
      </>, programsReady)}</motion.div>}</AnimatePresence>
      {openItems.length > 0 && section("open-work", text("Open work", "未完成事项"), openItems.map(openWorkRow))}
      {routines.length > 0 && section("scheduled", text("Scheduled tasks", "定时任务"), routines.map(routineRow), scheduledReady)}
    </div>
    {hasChart ? <div className="flex min-h-0 flex-1 flex-col px-2">
      <WorkChart key={sessionId} scopes={chartScopes} runsByHead={chartRuns} tasks={chartTasks} context={context} box={chartBox} memory={chartMemory} reportName={run => reportNameForRun(run, context)}
        marks={chartMarks} stoppable={stoppableScopes} nextWake={nextWakeOf(ongoing, currentScope.active, text)} onOpenNextWake={() => open({ kind: "main", id: currentScope.key })} />
    </div> : null}
  </section> : null}{pageLayer}</>;
}
