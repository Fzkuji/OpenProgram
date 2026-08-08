/**
 * Composer — chat input area.
 *
 * Owns: input value, attachments, slash menu, plus menu (Tools / Web
 * Search), thinking-effort selector, token badge, send/stop button.
 * Submits chat turns directly via the WS channel; no legacy globals.
 *
 * The pieces live next door: ./use-chat-submit (submit + stop),
 * ./modes/fn-form/use-fn-form-submit (function dispatch),
 * ./paste/use-paste-tokens (long-paste chips), ./use-history-recall
 * (↑/↓), ./use-composer-keydown (key precedence),
 * ./controls/controls-cluster (the bottom control row),
 * ./status-chip and ./scoped-drop-overlay. This file owns the mode
 * switch, the wrapper layout, and the send/stop affordance.
 *
 * Styling lives in ./composer.module.css. The page-level chat layout
 * (chat-area, welcome screen, message list, etc.) is still rendered
 * by the legacy template for the moment and will be migrated in
 * subsequent slices.
 */
"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";

import { useSessionStore } from "@/lib/session-store";
import { useSessionScope } from "@/lib/session-store/session-scope";
import { getSocket } from "@/lib/runtime-bridge/state";
import { api } from "@/lib/net/api";
import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";

// Session-scope chips relocated from the dismantled 48px topbar row —
// each carries its own popover menu (project-menu / agent-selector /
// permission-menu submodules under ../top-bar).
import { ProjectBadge, WorkingDirChips } from "../top-bar";
import { GoalChip, useSessionGoal } from "../goal-chip";
import { CircleHelp, Target } from "lucide-react";
import { visibleParams } from "./modes/fn-form/fn-form";
import { type DecisionAction } from "./modes/question/question-mode";
import { resolveComposerMode } from "./modes/resolve-mode";
import { SendIcon, StopIcon } from "./icons";
import { type AnimatedNavIconHandle } from "@/components/animated-icons";
import { type SlashCommand } from "./slash/slash-commands";
import { SlashMenu } from "./slash/slash-menu";
import { FileTiles } from "./attach/file-tiles";
import { useComposerAttachments } from "./attach/use-composer-attachments";
import { useFileMention } from "./attach/use-file-mention";
import { ImageAttachStrip } from "./attach/image-attach-strip";
import { useFnFormState } from "./modes/fn-form/use-fn-form-state";
import { useFnFormWrapper } from "./modes/fn-form/use-fn-form-wrapper";
import { useFnFormSubmit } from "./modes/fn-form/use-fn-form-submit";
import { useSlashMenu } from "./slash/use-slash-menu";
import { useThinkingEffort } from "./controls/use-thinking-effort";
import { useToolsToggles } from "./controls/use-tools-toggles";
import { ControlsCluster } from "./controls/controls-cluster";
import { usePasteTokens } from "./paste/use-paste-tokens";
import { useHistoryRecall } from "./use-history-recall";
import { useChatSubmit } from "./use-chat-submit";
import { useComposerKeyDown } from "./use-composer-keydown";
import { StatusChip } from "./status-chip";
import { ScopedDropOverlay } from "./scoped-drop-overlay";
import { ComposerBody } from "./composer-body";
import { QuestionPanel } from "./question-panel";
import { sendChatMessage } from "./legacy-send";
import styles from "./composer.module.css";

/* Single shared WebSocket, owned by `lib/net/use-ws.ts` and reached
   through `runtime-bridge/state`'s `getSocket()`. When the WS layer is
   migrated (next slice), this helper is replaced by ``useWS().send``
   and the call sites stay identical. */
function wsSend(payload: unknown): boolean {
  const sock = getSocket();
  if (!sock || sock.readyState !== WebSocket.OPEN) return false;
  try {
    sock.send(typeof payload === "string" ? payload : JSON.stringify(payload));
    return sock.readyState === WebSocket.OPEN;
  } catch (error) {
    console.error("[Composer] WebSocket send failed:", error);
    return false;
  }
}

const noop = () => {};

/**
 * @param boundSessionId  Render this composer against a SPECIFIC session
 *   instead of whichever one is focused. Split view passes it so each pane
 *   owns an independent composer (own draft, own settings, own run state,
 *   sends with `background: true`). Omitted — the default and the only
 *   pre-split behavior — everything below resolves to the focused session
 *   exactly as before.
 */
export function Composer({ sessionId: boundSessionId }: { sessionId?: string } = {}) {
  const { text } = useTranslation();
  const bound = boundSessionId ?? null;
  const focusedSessionId = useSessionStore((s) => s.currentSessionId);
  const focusedChatKey = useSessionStore((s) => s.activeChatKey);
  // A bound composer answers for ITS session on both axes; an unbound one
  // keeps the focused-session semantics verbatim.
  const currentSessionId = bound ?? focusedSessionId;
  const activeChatKey = bound ?? focusedChatKey;
  // Per-session running state: send/stop button binds to the current
  // session's running task, not a global flag. This is what lets the
  // user switch from a running session A to session B and immediately
  // send a new message in B while A is still streaming.
  const runningTask = useSessionScope((s) => s.running);
  // Draft text — this scope's, always. No focused-session fallback: the
  // single-session shell provides a scope too.
  const input = useSessionScope((s) => s.draft);
  const setInput = useSessionScope((s) => s.setDraft);
  // Submit captures the owner key up front and clears THAT chat's draft when
  // the async send resolves, which may be a different chat than this scope by
  // then. Goes through the global keyed setter, which pushes into the owner's
  // scope store as well as persisting.
  const setComposerInputFor = useSessionStore((s) => s.setComposerInputFor);
  const focusTick = useSessionStore((s) => s.composerFocusTick);

  const historyRecall = useHistoryRecall(bound, currentSessionId);
  const { setHistoryIndex } = historyRecall;

  // Attachments — pending images, pending docs, drag-drop, file
  // picker. All state + the window-level drop-routing live in the
  // hook now (see ./use-composer-attachments).
  const {
    pendingImages,
    imageError,
    pendingDocs,
    dragActive,
    fileInputRef,
    composerRootRef,
    addImagesForOwner,
    removeImage,
    setImageError,
    removeDoc,
    onPickImages,
    onFileInputChange,
    clearAfterSubmit: clearAttachmentsAfterSubmit,
  } = useComposerAttachments(bound);

  // Refs declared up-front so the @file mention hook below can
  // reference them. The other refs (wrapper, sendBtn, plus menu,
  // thinking pill) get declared in their original spot.
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { pastedEntries, pasteMissing, onPaste, removePaste } = usePasteTokens({
    input,
    setInput,
    activeChatKey,
    addImagesForOwner,
    setImageError,
  });

  const fnFormFunction = useSessionStore((s) => s.fnFormFunction);
  const closeFnFormStore = useSessionStore((s) => s.closeFnForm);
  const setFnFormClosing = useSessionStore((s) => s.setFnFormClosing);
  const setCurrentConv = useSessionStore((s) => s.setCurrentConv);
  // 系统等用户决定（runtime.ask / confirm / approval）的 FIFO 队列；队首占据
  // 输入区呈现为 question mode（docs/design/ui/composer-interaction-modes.md）。
  // 输入框状态跟会话走：只认「属于当前会话」的提问，切到别的会话就不显示
  // （也就不会误把答案发到别的会话上）。
  const pendingDecisions = useSessionStore((s) => s.pendingDecisions);
  const dequeueDecision = useSessionStore((s) => s.dequeueDecision);
  const pendingDecision =
    pendingDecisions.find((d) => d.sessionId === currentSessionId) ?? null;

  // 提问路由：ask/confirm（含 ask_user_question）和 goal waiting_user 都走
  // 输入框顶部的 QuestionPanel —— composer 的 textarea/底栏/env-chip 行原样
  // 不动，wrapper 只因多了这块面板向上长高。approval/form/ask_many 仍走旧的
  // morphed QuestionMode（activeDecision 路径）。真 ask 优先于 goal。
  const askDecision =
    pendingDecision &&
    (pendingDecision.kind === "ask" || pendingDecision.kind === "confirm")
      ? pendingDecision
      : null;
  const activeDecision = askDecision ? null : pendingDecision;
  // goal 挂起：answeredKey = 乐观收起（点 pill / 发消息后立即收，不等
  // goal_update 把 status 翻走）；按「会话:轮数:问题」记 key，下一次挂起
  // 自然重新出现。刷新后水合数据仍是 waiting_user → 面板重现。
  const goal = useSessionGoal(currentSessionId);
  const [goalAnsweredKey, setGoalAnsweredKey] = useState<string | null>(null);
  const goalKey =
    goal?.status === "waiting_user" && goal.last_question && currentSessionId
      ? `${currentSessionId}:${goal.turns_used ?? 0}:${goal.last_question}`
      : null;
  const goalWaiting = goalKey !== null && goalAnsweredKey !== goalKey;
  const send = wsSend;

  const isRunning = runningTask !== null;
  const fnFormActive = fnFormFunction !== null;
  // 右下角圆按钮是否该显示红色停止 ■：仅当任务真在跑、且当前没有 decision
  // 占据输入区。decision 在场时函数虽“运行”着，但它在等用户答题——此刻这个
  // 按钮要当“提交”用，不能变停止键（否则点了是中断函数，不是交答案）。
  // 跑着的时候输入框里有字 = 用户在写 steer（占位符就是这么提示的），
  // 此刻圆钮必须是发送：显示成停止键的话，点下去杀的是任务本身，写好的
  // 那句话一个字都没送出去。空输入才是停止键。
  // askDecision（顶部面板在等答案）时同理：圆钮要当「提交答案」用，不能变
  // 停止键。
  const showStop =
    isRunning && activeDecision === null && askDecision === null && !input.trim();

  // 输入框当前处于哪个 mode —— 一个显式的派生值（含优先级），渲染时按它
  // switch，不再散在 JSX 里嵌套三元。idle / fn-form / question / approval。
  const composerMode = resolveComposerMode(activeDecision, fnFormFunction);
  // 任何"输入框变形"态（fn-form / question / approval / form）都用 fn-form
  // 那套容器样式（header/body 两段式 + 分割线定位），所以 wrapper 的 mode
  // 类不只在 fn-form 时加。
  const morphed = composerMode !== "idle";

  // 冲突规则（docs/design/ui/composer-interaction-modes.md）：系统决定
  // （runtime.ask / approval）撞上用户主动开的 fn-form → 取消 fn-form，让
  // 系统决定占住输入区。用户主动开的东西丢弃无所谓；系统决定之间则由
  // pendingDecisions 的 FIFO 队列天然排队（队首占据，答完出下一个）。
  useEffect(() => {
    if ((activeDecision || askDecision) && fnFormFunction) {
      closeFnFormStore();
    }
  }, [activeDecision, askDecision, fnFormFunction, closeFnFormStore]);

  // @file mention — state + debounced /api/file-search + popover
  // positioning + picker all live in ./use-file-mention now. The hook
  // owns the 6 useStates + 2 effects + pickFile callback that used to
  // sit here.
  const fileMention = useFileMention({ input, setInput, textareaRef });

  // Thinking-effort + plus-menu + tools toggles each live in their own
  // dedicated hooks now — see ./use-thinking-effort, ./use-tools-toggles.
  const {
    thinking,
    options: thinkingOptions,
    menuOpen: thinkingMenuOpen,
    setMenuOpen: setThinkingMenuOpen,
    set: setThinking,
  } = useThinkingEffort();
  // The effort picker only appears once a chat model is actually
  // selected; with no model picked it stays hidden.
  const chatModel = useSessionStore((s) => s.agentSettings?.chat?.model);
  // Agent settings feed the relocated chat/exec model chips below —
  // same store slice the old topbar row read (populated by
  // `updateAgentBadges` in lib/runtime-bridge/providers.ts).
  const agentSettings = useSessionStore((s) => s.agentSettings);
  const chatAgent = agentSettings.chat || {};
  const execAgent = agentSettings.exec || {};
  // Authoritative "is there a model to run at all" signal — the SAME
  // enabled-models list the top-bar picker reads. ``providerInfo.model``
  // is NOT enough: it reflects whatever model the last runtime used
  // (often an agent-pinned default), so it stays truthy even after the
  // user disables every provider — and a send would then silently run
  // on that pinned model. Keying off the enabled list means "picker is
  // empty" ⇒ "send is blocked", matching what the user sees up top.
  const { data: enabledModels } = useQuery({
    queryKey: ["models-enabled"],
    queryFn: api.listEnabledModels,
  });
  const noEnabledModels = (enabledModels ?? []).length === 0;
  // Block a send/run when nothing is enabled and explain why with a
  // transient top toast — only fired on the send/run ATTEMPT, never a
  // persistent badge/hint — instead of silently routing the turn to a
  // pinned default.
  const promptNeedModel = useCallback(() => {
    showToast(
      text(
        "No model configured — enable one before sending.",
        "还没配置模型 — 发送前请先启用一个模型。",
      ),
      {
        tone: "warn",
        link: {
          label: text("Open Providers →", "去配置 Provider →"),
          href: "/settings/providers",
        },
      },
    );
  }, [text]);
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  const {
    tools: toolsEnabled,
    webSearch: webSearchEnabled,
    toggleTools,
    toggleWebSearch,
  } = useToolsToggles();
  // Per-turn "Fast" speed tier → sent as service_tier:"priority". Now
  // per-session (this scope's settings.fast, persisted + isolated per
  // chat like the other toggles). The backend forwards it to the provider
  // request body and no-ops for providers that don't read service_tier.
  const fastEnabled = useSessionScope((s) => s.settings.fast);
  // 有的模型没有 Fast 档（service_tier）——后端 agent_settings 按当前
  // 模型下发 chat.fast；不支持就整个隐藏开关/chip，也不随消息发送。
  const fastSupported = useSessionStore((s) => !!s.agentSettings?.chat?.fast);
  const setComposerSettings = useSessionScope((s) => s.patchSettings);
  const toggleFast = () => setComposerSettings({ fast: !fastEnabled });
  // Unattended toggle: nobody watching → withhold the agent's user-question
  // tool. Mirror the per-session UI flag to the backend via set_attended so
  // the tool-resolution gate matches (attended = !unattended).
  const unattended = useSessionScope((s) => s.settings.unattended);
  const toggleUnattended = () => {
    const next = !unattended;
    setComposerSettings({ unattended: next });
    if (currentSessionId) {
      send({ action: "set_attended", session_id: currentSessionId, attended: !next });
    }
  };
  // Sync the persisted per-session unattended flag to the backend whenever the
  // session changes (a fresh worker defaults to attended; this restores the
  // user's choice for this chat so the ask-tool gate matches the UI).
  useEffect(() => {
    if (currentSessionId) {
      send({ action: "set_attended", session_id: currentSessionId, attended: !unattended });
    }
  }, [currentSessionId, unattended, send]);

  // Tool profiles — fetch the list on mount so the "+" menu can show
  // a profile picker. The active profile determines which tools the
  // model gets for this conversation.
  const [toolProfiles, setToolProfiles] = useState<Record<string, string[]>>({});
  const [activeProfile, setActiveProfile] = useState("full");
  useEffect(() => {
    fetch("/api/tool-profiles")
      .then((r) => r.json())
      .then((d) => {
        setToolProfiles(d.profiles ?? {});
        setActiveProfile(d.active ?? "full");
      })
      .catch(() => {});
  }, []);

  const switchProfile = (name: string) => {
    setActiveProfile(name);
    // Persist the choice to the backend
    fetch("/api/tool-profiles/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }).catch(() => {});
  };

  // Slash-menu state lives in its own hook (./use-slash-menu).
  // fn-form field state (values, workdir, error highlight, closing
  // flag) is owned by `./use-fn-form-state`; it also runs the
  // default-value seeding effect on fn change.
  const fnForm = useFnFormState(fnFormFunction);
  const setFnFormClosingLocal = fnForm.setClosing;

  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const sendBtnRef = useRef<HTMLButtonElement>(null);
  // question / approval / form / ask_many 各 mode 把右下角该渲染的按钮组
  // （统一文字 pill：单题 [发送]，ask_many [上一题, 下一题/发送]）报给这个
  // state，由 composer 在发送按钮位呈现，取代圆形箭头。state（非 ref）才能
  // 让按钮的 disabled / 文案随选择 + 翻页实时更新。
  const [decisionAction, setDecisionAction] = useState<DecisionAction | null>(null);
  // Drives the animated send arrow from the whole button's hover.
  const sendIconRef = useRef<AnimatedNavIconHandle>(null);
  // `thinkingTriggerRef`: the effort pill expands inline (no portal).
  // Since it lives inside `.inputWrapper`, the wrapper-contains check
  // already covers clicks on it. The plus menu is now a base-ui Menu,
  // which owns its own placement + outside-click close — no trigger/menu
  // refs or measured position needed.
  const thinkingTriggerRef = useRef<HTMLDivElement>(null);
  // Bumping this remounts <ThinkingEffortPill/>, resetting its INTERNAL
  // expanded state to false — the only way to force-collapse it from
  // outside (it ignores the expanded prop and never self-closes; the
  // card dwells open until this remount fires on an outside click).
  const [effortEpoch, setEffortEpoch] = useState(0);
  // effort 卡真实开合（pill 内部 state 回传）——驱动 effortText 的
  // aria-expanded，卡开着时 HoverTip 不再冒 "Thinking effort" 黑提示。
  const [effortCardOpen, setEffortCardOpen] = useState(false);

  // Wrapper height transition (open / close / A→B switch crossfade)
  // is all in one hook — see `./use-fn-form-wrapper`. `outgoingFn`
  // drives the absolute-positioned crossfade overlay below.
  const { outgoingFn } = useFnFormWrapper({
    fnFormFunction,
    fnFormClosing: fnForm.closing,
    // Depend only on the two STABLE callbacks, not the whole `fnForm`
    // object — `useFnFormState` returns a fresh object every render, so
    // depending on it made `onCloseComplete` change identity each
    // render, which re-fired the wrapper's height-transition layout
    // effect on EVERY render. That restarted the open/close transition
    // mid-flight and made the send button jump.
    onCloseComplete: useCallback(() => {
      closeFnFormStore();
      setFnFormClosingLocal(false);
    }, [closeFnFormStore, setFnFormClosingLocal]),
    wrapperRef,
    sendBtnRef,
    // A system decision uses the same morphed container — drive the same
    // wrapper-grow + button-glide-to-bottom for it. Its id keys the
    // open-transition so each new decision re-pins the button.
    decisionKey: activeDecision?.id ?? null,
  });

  // Chat-mode resting spot for the action button is CSS top:10px (the
  // 24px button centers in the 44px single-line box). use-fn-form-wrapper resets
  // the inline top to the legacy 16px whenever a morphed state ends —
  // clear it here (this effect runs after the hook's, they share the
  // morphed trigger) so the stylesheet value wins again in chat mode.
  useEffect(() => {
    if (!morphed && sendBtnRef.current) sendBtnRef.current.style.top = "";
  }, [morphed]);

  // Publish the composer's REAL height as a root CSS var so the
  // transcript's bottom padding can reserve it. The main shell's
  // `.chat-messages` historically reserved a flat 25vh — enough for the
  // normal composer, but the question panel (goal 挂起 / ask) can grow the
  // stack past that and cover the last message's tail. Same mechanism the
  // split pane already uses (`--peer-composer-h` in peer-session-pane).
  // transcript.css takes max(25vh, this + 24px), so normal chats are
  // pixel-identical and only an overgrown composer widens the reserve.
  // The stick-to-bottom ResizeObserver on #chatMessages then re-pins, so
  // a reader parked at the bottom gets the last message pushed fully out.
  const inputAreaRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    // Split-pane composers measure into their own pane var — only the
    // focused shell's composer owns the root-level var.
    if (bound !== null) return;
    const el = inputAreaRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const apply = () => {
      const h = el.offsetHeight;
      if (h > 0) {
        document.documentElement.style.setProperty(
          "--main-composer-height",
          `${Math.round(h)}px`,
        );
      }
    };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => {
      ro.disconnect();
      document.documentElement.style.removeProperty("--main-composer-height");
    };
  }, [bound]);

  // Auto-resize the textarea as content changes.
  useEffect(() => {
    const t = textareaRef.current;
    if (!t) return;
    t.style.height = "auto";
    const nextHeight = Math.min(t.scrollHeight, 200);
    t.style.height = `${nextHeight}px`;
    t.style.overflowY = t.scrollHeight > 200 ? "auto" : "hidden";
  }, [input]);

  // External focus requests via the store (welcome buttons,
  // retry helpers, etc.).
  useEffect(() => {
    if (focusTick === 0) return;
    textareaRef.current?.focus();
  }, [focusTick]);

  // Close the effort pill on outside click. It's INLINE inside the
  // composer wrapper, so a click in the textarea or on any other
  // composer control should collapse it (otherwise the expanded state
  // lingers as the user keeps typing). We check `thinkingTriggerRef`
  // directly — anything outside the pill itself counts as outside.
  //
  // The plus menu no longer needs a handler here: it's a base-ui Menu
  // now, which owns its own outside-click / Escape close.
  useEffect(() => {
    function onDoc(ev: MouseEvent) {
      const t = ev.target as Node | null;
      if (!t) return;
      const wrapper = textareaRef.current?.closest(`.${styles.inputWrapper}`);
      if (!wrapper) return;

      if (
        thinkingTriggerRef.current &&
        !thinkingTriggerRef.current.contains(t)
      ) {
        setThinkingMenuOpen(false);
        // The pill never self-closes (no mouseleave collapse anymore) —
        // this outside-click IS the only way to shut the card. Remount
        // the pill to reset its internal expanded state to false.
        if (thinkingTriggerRef.current.querySelector("[data-effort-expanded]")) {
          setEffortEpoch((e) => e + 1);
          // remount 会把 pill 内部 expanded 归零，回传不会触发——手动同步。
          setEffortCardOpen(false);
        }
      }
    }
    // pointerdown 而非 click：点击滑轨档位会让 React 立刻重排刻度点，
    // click 冒泡到 document 时目标已被卸载，contains() 误判成"点了
    // 外面"而收起卡片。pointerdown 发生在重排前，判定可靠。
    document.addEventListener("pointerdown", onDoc);
    return () => document.removeEventListener("pointerdown", onDoc);
  }, [setThinkingMenuOpen]);

  // /context 面板开关放本会话的 scope —— badge（右下角圆环）负责渲染浮动
  // 弹窗，/context slash 命令只需把它打开，弹窗即锚定圆环向上展开。分屏时
  // 两个 composer 各自一份，点一个不会两边同时弹。
  const setContextPanelOpen = useSessionScope((s) => s.setContextPanelOpen);

  // Slash menu (state + open/close timing + command dispatch).
  const slash = useSlashMenu({
    boundSessionId: bound,
    input,
    textareaRef,
    send,
    openContextPanel: () => setContextPanelOpen(true),
  });

  /* ---- Submit -------------------------------------------------------- */

  const { submit, stop } = useChatSubmit({
    bound,
    input,
    activeChatKey,
    currentSessionId,
    isRunning,
    noEnabledModels,
    promptNeedModel,
    send,
    setComposerInputFor,
    setHistoryIndex,
    slash,
    pendingImages,
    pendingDocs,
    clearAttachmentsAfterSubmit,
    thinking,
    toolsEnabled,
    webSearchEnabled,
    fastEnabled,
    fastSupported,
  });

  // 顶部提问面板在场时的提交改道（唯一允许改输入框提交行为的地方，样式
  // 不动）：真 ask → 输入文本作为答案走 question_reply；goal → 普通聊天
  // 发送本身就是回答（goal 循环收任意用户消息），只需乐观收起面板。
  const submitWithPanel = useCallback(async () => {
    const trimmed = input.trim();
    if (askDecision && trimmed && askDecision.allow_custom) {
      send({
        action: "question_reply",
        id: askDecision.id,
        answer: askDecision.multi ? [trimmed] : trimmed,
      });
      dequeueDecision(askDecision.id);
      setComposerInputFor(activeChatKey ?? currentSessionId, "");
      return;
    }
    if (!askDecision && goalWaiting && !morphed) setGoalAnsweredKey(goalKey);
    await submit();
  }, [
    input,
    askDecision,
    send,
    dequeueDecision,
    setComposerInputFor,
    activeChatKey,
    currentSessionId,
    goalWaiting,
    morphed,
    goalKey,
    submit,
  ]);

  // Pick a slash command — argless commands run immediately, commands
  // that take arguments just fill the input so the user can type them.
  function selectSlashCommand(cmd: SlashCommand) {
    if (cmd.args) {
      setInput(`${cmd.name} `);
      requestAnimationFrame(() => textareaRef.current?.focus());
      return;
    }
    if (slash.runCommand(cmd.name)) {
      setInput("");
      slash.close();
    }
  }

  const onKeyDown = useComposerKeyDown({
    input,
    setInput,
    fileMention,
    historyRecall,
    slash,
    selectSlashCommand,
    submit: submitWithPanel,
  });

  function onMenuItemClick(cmd: SlashCommand) {
    selectSlashCommand(cmd);
  }

  /* ---- Function form submit ---------------------------------------- */

  // Close = mirror of open. Flip `fnFormClosing` so the
  // wrapper-height useLayoutEffect runs its shrink branch while the
  // form is still mounted; header/body fade out in parallel via the
  // `.closing` class. Store unmount happens after the height
  // transition ends (handled inside the useLayoutEffect).
  const handleFnFormClose = useCallback(() => {
    setFnFormClosingLocal(true);
    // Mirror into the store so the welcome screen flips its examples
    // row out of the collapsed state NOW — in sync with the form
    // shrinking — instead of a beat later when `fnFormFunction`
    // finally clears at transition end.
    setFnFormClosing(true);
  }, [setFnFormClosingLocal, setFnFormClosing]);

  const submitFnForm = useFnFormSubmit({
    fnFormFunction,
    fnForm,
    currentSessionId,
    activeChatKey,
    isRunning,
    noEnabledModels,
    promptNeedModel,
    send,
    setCurrentConv,
    handleFnFormClose,
  });

  // decision 在场时右下角是 mode 自己的 navButtons 按钮组（见 JSX），不走
  // 这个圆形按钮，所以这里只管 fn-form / 普通聊天两种。
  const onSendButtonClick = fnFormActive ? submitFnForm : submitWithPanel;

  // Goal 挂起提问卡点选项 = 把选项 label 当一条普通聊天消息发出去，与
  // 手打回车同一条路：跑着 → steer；空闲 → sendChatMessage（乐观气泡 /
  // welcome 隐藏 / running 翻转都由它做）。true = 已发出，卡片乐观隐藏。
  const sendGoalAnswer = useCallback(
    (label: string): boolean => {
      const owner = activeChatKey ?? currentSessionId;
      const trimmed = label.trim();
      if (!owner || !trimmed) return false;
      if (isRunning) {
        return send({ action: "steer", session_id: owner, message: trimmed });
      }
      if (noEnabledModels) {
        promptNeedModel();
        return false;
      }
      return sendChatMessage({
        text: trimmed,
        sessionId: owner,
        background: bound !== null,
        thinking,
        toolsEnabled,
        webSearchEnabled,
        serviceTier: fastEnabled && fastSupported ? "priority" : undefined,
      });
    },
    [
      activeChatKey,
      currentSessionId,
      isRunning,
      noEnabledModels,
      promptNeedModel,
      send,
      bound,
      thinking,
      toolsEnabled,
      webSearchEnabled,
      fastEnabled,
      fastSupported,
    ],
  );

  // 拒绝/取消当前的系统决定 —— 走左上角 ✕。发 question_reject 并即时出队
  // （后端 _resolve_question 收口 + 广播）。
  const rejectDecision = useCallback(() => {
    const d = activeDecision;
    if (!d) return;
    const sock = getSocket();
    if (sock && sock.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ action: "question_reject", id: d.id }));
    }
    dequeueDecision(d.id);
  }, [activeDecision, dequeueDecision]);

  // 顶部提问面板的内容（真 ask 优先；morphed 时不叠面板 —— approval/form
  // 占着输入区，答完再出）。点 pill = 立即提交（点击即时反馈）。
  const panel = askDecision
    ? {
        badge: (
          <>
            <CircleHelp size={13} strokeWidth={2} />
            <span>{text("Your input is needed", "需要你的输入")}</span>
          </>
        ),
        prompt: askDecision.prompt,
        options: askDecision.options.map((label) => ({ label })),
        onPick: (label: string) => {
          send({
            action: "question_reply",
            id: askDecision.id,
            answer: askDecision.multi ? [label] : label,
          });
          dequeueDecision(askDecision.id);
        },
      }
    : goalWaiting && !morphed
    ? {
        badge: (
          <>
            <Target size={13} strokeWidth={2} />
            <span>{text("goal — waiting for your answer", "goal · 等你回答")}</span>
          </>
        ),
        prompt: goal!.last_question!,
        options: (goal!.last_question_options ?? []).map((o) => ({
          label: o.label,
          description: o.description || undefined,
        })),
        onPick: (label: string) => {
          if (sendGoalAnswer(label)) setGoalAnsweredKey(goalKey);
        },
      }
    : null;
  // In chat mode: disabled when textarea is empty OR when a paste
  //   token references content that was lost (chip is red). Submitting
  //   in the "lost" state would silently strip the token — see the
  //   submit() guard mirror.
  // In fn-form mode: disabled when any required param has no value,
  //   OR when workdir is required and empty. Also surface WHICH field
  //   is blocking via the title attribute so hovering over a greyed
  //   send button explains why nothing happens on click.
  const missingFnParams: string[] = (() => {
    if (!fnFormActive) return [];
    const fn = fnFormFunction!;
    const out: string[] = [];
    const workdirMode = fn.workdir_mode ?? "optional";
    if (workdirMode === "required" && !fnForm.workdir.trim()) {
      out.push("work_dir");
    }
    for (const p of visibleParams(fn)) {
      if (!p.required) continue;
      const v = String(fnForm.values[p.name] ?? "").trim();
      if (!v) out.push(p.name);
    }
    return out;
  })();
  // In fn-form mode we no longer disable the send button just because
  // a required field is empty — a disabled button doesn't fire onClick,
  // so the user gets zero feedback ("点了没反应"). Keep it enabled and
  // let submitFnForm's setError path light up the missing field's red
  // border instead. The button still LOOKS dim (data-fn-missing) and
  // its title spells out which field is blocking.
  const sendDisabled = fnFormActive
    ? false
    : !input.trim() || pasteMissing.size > 0;
  const sendTitle = fnFormActive
    ? missingFnParams.length > 0
      ? text(
          `Fill required field${missingFnParams.length > 1 ? "s" : ""}: ${missingFnParams.join(", ")}`,
          `请填写必填字段：${missingFnParams.join(", ")}`,
        )
      : text("Run", "运行")
    : pasteMissing.size > 0
    ? text("Paste content lost. Remove the red chip and re-paste.", "粘贴内容已丢失。请移除红色标签后重新粘贴。")
    : text("Send message", "发送消息");

  /* ---- Render -------------------------------------------------------- */

  // Controls cluster — permission / plus menu / tool chips on the
  // left; model texts + effort pill + context ring on the right.
  // Always rendered in the detached .controlsRow below the wrapper, in
  // every mode — opening a fn-form / question only grows the wrapper
  // above it, the controls row itself never moves or restyles.
  const controlsCluster = (
    <ControlsCluster
      bound={bound}
      plusMenuOpen={plusMenuOpen}
      setPlusMenuOpen={setPlusMenuOpen}
      onPickImages={onPickImages}
      pendingImagesCount={pendingImages.length}
      pendingDocsCount={pendingDocs.length}
      toolsEnabled={toolsEnabled}
      toggleTools={toggleTools}
      webSearchEnabled={webSearchEnabled}
      toggleWebSearch={toggleWebSearch}
      fastEnabled={fastEnabled}
      fastSupported={fastSupported}
      toggleFast={toggleFast}
      unattended={unattended}
      toggleUnattended={toggleUnattended}
      toolProfiles={toolProfiles}
      activeProfile={activeProfile}
      switchProfile={switchProfile}
      chatAgent={chatAgent}
      execAgent={execAgent}
      chatModel={chatModel}
      noEnabledModels={noEnabledModels}
      thinking={thinking}
      thinkingOptions={thinkingOptions}
      setThinking={setThinking}
      thinkingMenuOpen={thinkingMenuOpen}
      setThinkingMenuOpen={setThinkingMenuOpen}
      thinkingTriggerRef={thinkingTriggerRef}
      effortEpoch={effortEpoch}
      effortCardOpen={effortCardOpen}
      setEffortCardOpen={setEffortCardOpen}
    />
  );

  return (
    <div ref={inputAreaRef} className={styles.inputArea}>
      {/* Drop overlay scoped to the chat main column (#chatArea) —
          covers the conversation surface but lets the sidebars stay
          interactive. ``dragActive`` is set by the window-level
          drag listeners in useComposerAttachments; the actual file
          handling lives there too. ``mainRect`` is recomputed on
          drag enter so the overlay tracks layout / window-resize
          changes between drags. */}
      {dragActive && typeof document !== "undefined"
        ? createPortal(
            <ScopedDropOverlay />,
            document.body,
          )
        : null}
      {/* Env chips — floating row ABOVE the input box (Claude Code
          arrangement): filled pill chips [Local] [📁 project] only.
          The add-folder entry stays inside the ProjectMenu popover
          ("Open folder…"), it is not a standalone control. Sits
          OUTSIDE .composerStack so the slash menu's bottom:100% anchor
          still lands on the wrapper top edge; .inputArea is bottom-
          anchored absolute, so this row grows the composer upward
          without shifting the transcript. */}
      <div className={styles.envChips}>
        <StatusChip owningId={bound === null} />
        <ProjectBadge />
        <WorkingDirChips />
        <GoalChip />
        {/* DAG 视角的画布控件（Fit / 缩放 / 图例）从 dag-view.tsx
            portal 进来，落在输入框右上角——env chip 行的右端。 */}
        <div id="dagHudSlot" className={styles.dagHudSlot} />
      </div>
      {/* composerStack wraps {slashClip, inputWrapper} so the slash
          menu's vertical anchor is the wrapper's top edge — not a
          magic-number offset from the inputArea bottom. composerStack
          is position:relative and naturally takes inputWrapper's
          height (slashClip is absolute, doesn't contribute), so
          slashClip's bottom:100% lands exactly at the wrapper top. */}
      <div className={styles.composerStack}>
      <div className={styles.slashClip}>
        <SlashMenu
          visible={slash.visible}
          closing={slash.closing}
          matches={slash.matches}
          activeIndex={slash.activeIndex}
          onPick={onMenuItemClick}
        />
      </div>

      <div
        ref={(el) => {
          // wrapperRef tracks the styled box; composerRootRef is the
          // outer drop zone — same element here.
          wrapperRef.current = el;
          composerRootRef.current = el;
        }}
        className={styles.inputWrapper}
      >
        {/* 提问面板 —— wrapper 顶部向上生长的附加区（goal 挂起 / 真 ask）。
            下面的 textarea / 底栏 / 圆形发送按钮全部原样。 */}
        {panel && (
          <QuestionPanel
            badge={panel.badge}
            prompt={panel.prompt}
            options={panel.options}
            onPick={panel.onPick}
          />
        )}
        <ImageAttachStrip
          pendingImages={pendingImages}
          imageError={imageError}
          fileInputRef={fileInputRef}
          onFileInputChange={onFileInputChange}
          onRemove={removeImage}
          onDismissError={() => setImageError(null)}
        />
        <FileTiles docs={pendingDocs} onRemove={removeDoc} />

        <ComposerBody
          bound={bound}
          composerMode={composerMode}
          activeDecision={activeDecision}
          dequeueDecision={dequeueDecision}
          setDecisionAction={setDecisionAction}
          fnFormFunction={fnFormFunction}
          fnForm={fnForm}
          handleFnFormClose={handleFnFormClose}
          submitFnForm={submitFnForm}
          outgoingFn={outgoingFn}
          textareaRef={textareaRef}
          input={input}
          setInput={setInput}
          isRunning={isRunning}
          runningPlaceholder={text(
            "type to steer the running task…",
            "输入以干预正在运行的任务…",
          )}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          slash={slash}
          fileMention={fileMention}
          pastedEntries={pastedEntries}
          pasteMissing={pasteMissing}
          removePaste={removePaste}
        />

        {/* Single send/stop button anchored at the wrapper level.
            `top` is mutated via inline style by the wrapper-height
            useLayoutEffect so the button glides between its chat-mode
            position and the morphed-mode bottom-right corner over the
            same 0.3s curve as the wrapper itself — one continuous
            motion instead of a row-to-row teleport.
            统一：任何 decision（单选/多选/确认/批准/表单/ask_many）在场时，
            这个位置都换成该 mode 报来的 navButtons 文字按钮组——单题是一颗
            「发送」，ask_many 是「上一题 / 下一题（末题→发送）」。绝不出现
            圆形箭头或红色停止 ■。圆形按钮只在普通聊天 / fn-form 时出现。 */}
        {activeDecision ? (
          <div ref={sendBtnRef as unknown as React.RefObject<HTMLDivElement>} className={styles.decisionNav}>
            {(decisionAction?.navButtons ?? []).map((b, i) => (
              <button
                key={i}
                type="button"
                className={`${styles.decisionNavBtn} ${b.primary ? styles.decisionNavBtnPrimary : ""}`}
                onClick={b.onClick}
                disabled={b.disabled}
              >
                {b.label}
              </button>
            ))}
          </div>
        ) : (
          /* decision 在场时（即便函数正“运行”——它其实在等用户答题），这个
             按钮必须是“提交”语义，绝不能显示成红色停止 ■：用户此刻要的是
             交答案，不是中断函数。所以 showStop 把 decision 排除在外。 */
          <button
            ref={sendBtnRef}
            className={`${styles.actionBtn} ${showStop ? styles.stopBtn : styles.sendBtn}`}
            onClick={showStop ? stop : onSendButtonClick}
            disabled={!showStop && sendDisabled}
            data-fn-missing={
              !showStop && fnFormActive && missingFnParams.length > 0
                ? "true"
                : undefined
            }
            onMouseEnter={() => sendIconRef.current?.startAnimation?.()}
            onMouseLeave={() => sendIconRef.current?.stopAnimation?.()}
            title={showStop ? text("Stop", "停止") : sendTitle}
            type="button"
          >
            {showStop ? <StopIcon /> : <SendIcon ref={sendIconRef} />}
          </button>
        )}

        {/* 右上角 —— wrapper 级，跨 fn-form 切换不闪。decision 在场时是
            「聊聊这个」文字 pill（放弃按它问的来、直接就这话题聊 = reject 当前
            decision 回到普通输入）；fn-form 时是 ✕ 关闭键。 */}
        {activeDecision ? (
          <button
            className={styles.chatAboutBtn}
            type="button"
            onClick={rejectDecision}
            onMouseDown={(e) => e.preventDefault()}
            tabIndex={-1}
            title={text("Chat about this instead", "直接聊这个")}
          >
            {text("Chat about this", "Chat about this")}
          </button>
        ) : (fnFormActive && !fnForm.closing) && (
          <button
            className={styles.closeBtn}
            type="button"
            onClick={handleFnFormClose}
            onMouseDown={(e) => e.preventDefault()}
            tabIndex={-1}
            title={text("Close", "关闭")}
            aria-label={text("Close", "关闭")}
          >
            <svg viewBox="0 0 12 12" width="14" height="14" aria-hidden="true">
              <path
                d="M2 2L10 10M10 2L2 10"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </svg>
          </button>
        )}
      </div>
      </div>{/* /.composerStack */}
      <div className={`${styles.controlsRow} composer-bottom-row`}>
        {controlsCluster}
      </div>
    </div>
  );
}
