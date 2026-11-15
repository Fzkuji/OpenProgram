/**
 * Composer — chat input area.
 *
 * Owns: input value, attachments, slash menu, plus menu (Tools / Web
 * Search), thinking-effort selector, token badge, send/stop button.
 * Submits chat turns through WS and exact function expressions through the
 * same HTTP dispatcher as the inline FunctionForm.
 *
 * The pieces are grouped by responsibility: ./submit (submit + stop),
 * ./modes/fn-form/use-function-dispatch (function dispatch),
 * ./modes/fn-form/use-fn-form-submit (form normalization),
 * ./paste/use-paste-tokens (long-paste chips), ./input (textarea,
 * history recall and key precedence),
 * ./controls/controls-cluster (the bottom control row),
 * ./environment-row/environment-row and ./attach/scoped-drop-overlay. This file owns the mode
 * switch, the wrapper layout, and the send/stop affordance.
 *
 * The shell styles live in ./composer.module.css; component-specific styles
 * live beside their components. The page-level chat layout (chat-area,
 * welcome screen, message list, etc.) is still rendered by the legacy
 * template for the moment.
 */
"use client";

import { platformKeys } from "@/lib/shortcut-keys";
import { PendingDecisionPanels } from "../messages/runtime/decision-output";
import { QueuedMessages } from "../messages/editing/queued-messages";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCompactControlsRow } from "./controls/use-compact-controls-row";
import { createPortal } from "react-dom";

import { useBoundComposerSettings, useBoundSetComposerSettings } from "./state/use-composer-settings";
import { settingOrigin } from "@/lib/session-store/branch-settings";
import { useSessionStore } from "@/lib/session-store";
import { useAwaitingAnswer } from "@/lib/chat/decisions/answer-wait";
import { useSessionScope } from "@/lib/session-store/session-scope";
import { getSocket } from "@/lib/runtime-bridge/state";
import { sendRuntimeCommand } from "@/lib/runtime-bridge/session/session-load";
import { useTranslation } from "@/lib/i18n";

// Session-scope chips relocated from the dismantled 48px topbar row —
// each carries its own popover menu (project-menu / agent-selector /
// permission-menu submodules under ../top-bar).
import { visibleParams } from "./modes/fn-form/fn-form";
import { resolveComposerMode } from "./modes/resolve-mode";
import { SendIcon, StopIcon } from "./icons";
import { type AnimatedNavIconHandle } from "@/components/animated-icons";
import { SolarIcon } from "@/components/solar-icons";
import { type SlashCommand } from "./slash/slash-commands";
import { SlashMenu } from "./slash/slash-menu";
import { AttachmentStrip } from "./attach/strip/attachment-strip";
import { attachmentsBlockSend } from "./attach/attachment-session-cache";
import { forgetRefill, setRewindUploading } from "@/lib/chat/rewind/rewind-store";
import { DROP_PANE, useComposerAttachments } from "./attach/use-composer-attachments";
import { useFileMention } from "./attach/mention/use-file-mention";
import type { MentionSources } from "./attach/mention/mention-sources";
import type { MentionScope } from "./attach/mention/at-mention";
import { showToast } from "@/lib/format-utils/toast";
import { useCenterTabs } from "@/lib/tabs/center-tabs-store";
import { runningApps } from "@/lib/net/computer-use-apps";
import { onComposerFiles, takeComposerFiles } from "@/lib/chat/composer-inbox";
import { useFnFormState } from "./modes/fn-form/use-fn-form-state";
import { useFnFormWrapper } from "./modes/fn-form/use-fn-form-wrapper";
import { useFnFormSubmit } from "./modes/fn-form/use-fn-form-submit";
import { useFunctionDispatch } from "./modes/fn-form/use-function-dispatch";
import { useSlashMenu } from "./slash/use-slash-menu";
import { useThinkingEffort } from "./controls/effort/use-thinking-effort";
import { useSessionAgentSettings } from "./state/use-session-agent-settings";
import { useToolsToggles } from "./controls/toggles/use-tools-toggles";
import { useModelAvailability } from "./controls/toggles/use-model-availability";
import { useUnattendedMode } from "./controls/toggles/use-unattended-mode";
import { useSandboxToggle } from "./controls/toggles/use-sandbox-toggle";
import { ControlsCluster } from "./controls/controls-cluster";
import { RIM_OUT, useTuckedControls } from "./controls/use-tucked-controls";
import { TuckRow } from "./controls/tuck-row";
import { useTuckBackdrop } from "./controls/use-tuck-backdrop";
import { usePasteTokens } from "./paste/use-paste-tokens";
import { useHistoryRecall } from "./input/use-history-recall";
import { useChatSubmit } from "./submit/use-chat-submit";
import "./submit/send-chat-message";
import { useComposerKeyDown } from "./input/use-composer-keydown";
import { useNextPrompt } from "./input/use-next-prompt";
import { useComposerInputEffects } from "./input/use-composer-input-effects";
import { EnvironmentRow } from "./environment-row/environment-row";
import { ScopedDropOverlay } from "./attach/scoped-drop-overlay";
import { ComposerBody } from "./modes/composer-body";
import { useVoiceInput } from "./voice/use-voice-input";
import { MicButton, VoiceStrip } from "./voice/voice-controls";
import styles from "./composer.module.css";
import { RecordDialog } from "../recording/record-dialog";
import { RecordingTile } from "../recording/recording-card";
import {
  composerKey, listenForRecordingResults, recordingAvailable, useRecordingStore,
} from "@/lib/recording/recording-store";
import { recordingEnabled } from "@/lib/recording/recording-application";
import { refreshApplications, useApplicationList } from "@/lib/net/application-list";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/* Single shared WebSocket, owned by `lib/net/ws/use-ws.ts` and reached
   through `runtime-bridge/state`'s `getSocket()`. When the WS layer is
   migrated (next slice), this helper is replaced by ``useWS().send``
   and the call sites stay identical. */
export function wsSend(payload: unknown): boolean {
  const sock = getSocket();
  if (!sock || sock.readyState !== WebSocket.OPEN) return false;
  try {
    if (typeof payload !== "string") return sendRuntimeCommand(payload) && sock.readyState === WebSocket.OPEN;
    sock.send(payload);
    return sock.readyState === WebSocket.OPEN;
  } catch (error) {
    console.error("[Composer] WebSocket send failed:", error);
    return false;
  }
}

// An @ in the draft can name an open web tab or a running app, not only a file.
const MENTION_SOURCES: MentionSources = { tabs: () => useCenterTabs.getState().tabs, apps: runningApps };

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

  const historyRecall = useHistoryRecall(bound, currentSessionId, input);
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
    addFiles,
    removeImage,
    replaceImage,
    setImageError,
    removeDoc,
    onPickImages,
    onFileInputChange,
    clearAfterSubmit: clearAttachmentsAfterSubmit,
    detachForSubmit: detachAttachmentsForSubmit,
  } = useComposerAttachments(bound, undefined, true);

  // Files sent here from elsewhere in the window (a web tab's region
  // screenshot) arrive as if dropped on this composer.
  useEffect(() => {
    if (!activeChatKey) return;
    const take = () => {
      const files = takeComposerFiles(activeChatKey);
      if (files.length) void addFiles(files);
    };
    take();
    return onComposerFiles(take);
  }, [activeChatKey, addFiles]);

  // Refs declared up-front so the @file mention hook below can
  // reference them. The other refs (wrapper, sendBtn, plus menu,
  // thinking pill) get declared in their original spot.
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { pasteMissing, onPaste } = usePasteTokens({
    input,
    setInput,
    activeChatKey,
    addImagesForOwner,
    addFiles,
    setImageError,
  });

  const fnFormFunction = useSessionStore((s) => s.fnFormFunction);
  const fnFormSubmitAction = useSessionStore((s) => s.fnFormSubmitAction);
  const closeFnFormStore = useSessionStore((s) => s.closeFnForm);
  const setFnFormClosing = useSessionStore((s) => s.setFnFormClosing);
  const setCurrentConv = useSessionStore((s) => s.setCurrentConv);
  const send = wsSend;
  const isRunning = runningTask !== null;
  const modKey = platformKeys("⌘");
  const isCancelling = Boolean(runningTask?.cancelling);
  const fnFormActive = fnFormFunction !== null;
  // Decisions have their own output cards. Chat text always sends or queues
  // a message. While a run goes on the action button is Stop, draft or not:
  // Enter adds typed text to the turn or queues it, and Esc stops too.
  // A run waiting on a request above still runs: Stop cancels it, and text
  // typed here (sent with Enter) replies to the request.
  const awaitingAnswer = useAwaitingAnswer(activeChatKey ?? currentSessionId) && !isRunning;
  const showStop = (isRunning || awaitingAnswer) && !fnFormActive;
  const composerMode = resolveComposerMode(fnFormFunction);
  const morphed = composerMode !== "idle";
  // The chat this composer writes to, and where its `@path` mentions
  // resolve: that chat's working directory, or for a chat not created yet
  // the project picked for it.
  const draftOwner = activeChatKey ?? currentSessionId;
  const pendingProjectId = useSessionStore((s) => (draftOwner ? s.pendingProjectsByChat[draftOwner] : undefined));
  const mentionScope = useMemo<MentionScope | null>(
    () => (draftOwner ? { sessionId: draftOwner, projectId: pendingProjectId ?? null } : null),
    [draftOwner, pendingProjectId],
  );
  // While the socket is down a message waits in the send queue; say so
  // once the connection has been up (not during the first connect).
  const wsStatus = useSessionStore((s) => s.wsStatus);
  const everConnected = useRef(false);
  if (wsStatus === "open") everConnected.current = true;
  const disconnected = everConnected.current && wsStatus !== "open";

  // @file mention — state + debounced /api/file-search + popover
  // positioning + picker all live in ./use-file-mention now. The hook
  // owns the 6 useStates + 2 effects + pickFile callback that used to
  // sit here.
  const fileMention = useFileMention({ input, setInput, textareaRef, sources: MENTION_SOURCES, scope: mentionScope });

  // Thinking-effort + plus-menu + tools toggles each live in their own
  // dedicated hooks now — see ./use-thinking-effort, ./use-tools-toggles.
  const {
    thinking,
    options: thinkingOptions,
    menuOpen: thinkingMenuOpen,
    setMenuOpen: setThinkingMenuOpen,
    set: setThinking,
  } = useThinkingEffort();
  // Agent settings feed the model chip below — this conversation's own
  // (a split pane never shows or changes the other pane's model).
  const agentSettings = useSessionAgentSettings();
  const chatAgent = agentSettings.chat || {};
  // The effort picker only appears once a chat model is actually
  // selected; with no model picked it stays hidden.
  const chatModel = chatAgent.model;
  const { noEnabledModels, promptNeedModel } = useModelAvailability();
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  // Demonstration recording: the + menu item, its start dialog and the
  // finished recording waiting to go out with the next message.
  const recordingComposer = composerKey(bound);
  const pendingRecording = useRecordingStore((s) => s.pending[recordingComposer]);
  const activeRecording = useRecordingStore((s) => s.active);
  const [recordDialogOpen, setRecordDialogOpen] = useState(false);
  const [canRecord, setCanRecord] = useState(false);
  useEffect(() => {
    setCanRecord(recordingAvailable());
    listenForRecordingResults();
  }, []);
  // The recorder is the built-in application workflow_recording: the item
  // shows only while it is enabled, and a recording left waiting when it
  // is switched off goes with it.
  const { builtin: builtinApplications, loaded: applicationsLoaded } = useApplicationList();
  const recordingOn = recordingEnabled(builtinApplications);
  useEffect(() => {
    if (canRecord) void refreshApplications().catch(() => {});
  }, [canRecord]);
  useEffect(() => {
    if (applicationsLoaded && !recordingOn && pendingRecording) {
      useRecordingStore.getState().setPending(recordingComposer, null);
    }
  }, [applicationsLoaded, recordingOn, pendingRecording, recordingComposer]);
  const {
    webSearch: webSearchEnabled,
    toggleWebSearch,
  } = useToolsToggles();
  // Per-turn "Fast" speed tier → sent as service_tier:"priority". Now
  // per-session (this scope's settings.fast, persisted + isolated per
  // chat like the other toggles). The backend forwards it to the provider
  // request body and no-ops for providers that don't read service_tier.
  const fastByModel = useSessionScope((s) => s.settings.fastByModel);
  const fastModelKey = `${chatAgent.provider ?? ""}:${chatAgent.model ?? ""}`;
  const fastDefault = useSessionScope((s) => s.settings.fast);
  const fastEnabled = fastByModel?.[fastModelKey] ?? fastDefault;
  // 有的模型没有 Fast 档（service_tier）——后端 agent_settings 按当前
  // 模型下发 chat.fast；不支持就整个隐藏开关/chip，也不随消息发送。
  const fastSupported = !!chatAgent.fast;
  const setComposerSettings = useBoundSetComposerSettings();
  // Web search follows the Agent unless this branch set it (the chip's tooltip says which).
  const webSearchOrigin = settingOrigin(useBoundComposerSettings().resolved, "web_search");
  const toggleFast = () => setComposerSettings({ fast: !fastEnabled, fastByModel: { ...fastByModel, [fastModelKey]: !fastEnabled } });
  const fastHint = chatAgent.fast_capability?.status === "unsupported"
    ? text("Fast is disabled for this connection.", "当前线路已禁用高速模式。")
    : !fastSupported
    ? text("Fast has not been verified for this connection.", "当前线路尚未确认支持高速模式。")
    : chatAgent.fast_capability?.source === "xai-api"
      ? text("Fast · Priority processing, 2× token price. Thinking effort is unchanged.", "高速 · 优先处理，token 单价为标准档的 2 倍。思考强度不变。")
      : text("Fast · Increased usage may apply. Thinking effort is unchanged.", "高速 · 可能增加用量。思考强度不变。");
  const { unattended, toggleUnattended } = useUnattendedMode(
    currentSessionId,
    send,
  );
  const { sandbox, sandboxAvailable, sandboxReason, toggleSandbox } =
    useSandboxToggle(activeChatKey ?? currentSessionId, !!currentSessionId);

  // Slash-menu state lives in its own hook (./use-slash-menu).
  // fn-form field state (values, error highlight, closing
  // flag) is owned by `./use-fn-form-state`; it also runs the
  // default-value seeding effect on fn change.
  const fnForm = useFnFormState(fnFormFunction);
  const setFnFormClosingLocal = fnForm.setClosing;

  const wrapperRef = useRef<HTMLDivElement | null>(null);
  // Chat mode only: the function form keeps its own layout.
  const tuck = useTuckedControls(wrapperRef, !fnFormActive);
  const controlsRowRef = useRef<HTMLDivElement | null>(null);
  useCompactControlsRow(controlsRowRef);
  // Voice input dictates into this chat's draft (chat mode only).
  const voice = useVoiceInput({
    textareaRef,
    input,
    setInput,
    wrapperRef,
    disabled: fnFormActive,
    owner: draftOwner,
  });
  // Drives the animated send arrow from the whole button's hover.
  const sendIconRef = useRef<AnimatedNavIconHandle>(null);
  // `thinkingTriggerRef`: the effort pill expands inline (no portal).
  // Since it lives inside `.inputWrapper`, the wrapper-contains check
  // already covers clicks on it. The plus menu is now a base-ui Menu,
  // which owns its own placement + outside-click close — no trigger/menu
  // refs or measured position needed.
  const thinkingTriggerRef = useRef<HTMLDivElement>(null);
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
  });

  const inputAreaRef = useComposerInputEffects({
    bound,
    input,
    focusTick,
    textareaRef,
  });
  // The mask above the box drops to just above it while the rows are tucked.
  useTuckBackdrop(inputAreaRef, tuck.tucked);

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
      // Do not require the chat textarea wrapper: fn-form unmounts it,
      // and that used to skip this handler so the effort card stuck.
      if (
        thinkingTriggerRef.current &&
        !thinkingTriggerRef.current.contains(t)
      ) {
        setThinkingMenuOpen(false);
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

  const dispatchFunction = useFunctionDispatch({
    currentSessionId,
    activeChatKey,
    background: bound !== null,
    isRunning,
    noEnabledModels,
    promptNeedModel,
    send,
    setCurrentConv,
  });

  const { submit: submitDraft, stop, runSlash } = useChatSubmit({
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
    detachAttachmentsForSubmit,
    thinking,
    webSearchEnabled,
    fastEnabled,
    fastSupported,
    dispatchFunction,
    mentionScope,
  });
  // Dictated text is still arriving: sending now would send half of it and
  // type the rest into an emptied box.
  const submit = (mode?: Parameters<typeof submitDraft>[0]) => {
    if (voice.state !== "idle") {
      voice.stop();
      showToast(text(
        "Dictation is still being transcribed. Send when the text has arrived.",
        "听写还在转写，文字出来后再发送。",
      ));
      return;
    }
    // What leaves the composer stops being a refill; a command does not.
    if (draftOwner && !input.trimStart().startsWith("/")) forgetRefill(draftOwner);
    return submitDraft(mode);
  };

  // Pick a slash command. Commands with a REQUIRED argument (an
  // `<angle-bracket>` placeholder in `args`) fill the input so the
  // user can type it. Everything else — no args, or only optional
  // `[bracketed]` ones like `/compact [keep_recent_tokens]` — runs
  // immediately; selecting from the menu means "do it now".
  function selectSlashCommand(cmd: SlashCommand) {
    if (cmd.args && cmd.args.includes("<")) {
      setInput(`${cmd.name} `);
      requestAnimationFrame(() => textareaRef.current?.focus());
      return;
    }
    runSlash(cmd.name);
  }

  const composerKeyDown = useComposerKeyDown({
    input,
    setInput,
    fileMention,
    historyRecall,
    slash,
    selectSlashCommand,
    submit,
    // Esc stops a running turn whatever the draft holds; the draft stays.
    canStop: isRunning && !fnFormActive && !isCancelling,
    stop,
  });
  const nextPrompt = useNextPrompt(currentSessionId, isRunning, input);
  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Tab takes the suggested next message as a draft; it is not sent.
    if (e.key === "Tab" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey && nextPrompt.suggestion) {
      e.preventDefault();
      setInput(nextPrompt.suggestion);
      nextPrompt.clear();
      return;
    }
    composerKeyDown(e);
  };

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
    dispatchFunction,
    handleFnFormClose,
  });

  // The send button is Enter: during a run it adds the message to the
  // current turn; ⌘/Ctrl+Enter is the keyboard path that queues instead.
  const onSendButtonClick = fnFormActive ? submitFnForm : () => void submit("steer");

  // In chat mode: disabled when textarea is empty OR when a paste
  //   token references content that was lost (chip is red). Submitting
  //   in the "lost" state would silently strip the token — see the
  //   submit() guard mirror.
  // In fn-form mode: disabled when any required param has no value,
  //   Also surface WHICH field is blocking via the title attribute so hovering over a greyed
  //   send button explains why nothing happens on click.
  const missingFnParams: string[] = (() => {
    if (!fnFormActive) return [];
    const fn = fnFormFunction!;
    const out: string[] = [];
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
  const attachmentBlock = attachmentsBlockSend(pendingImages, pendingDocs);
  const attachmentsLoading = attachmentBlock === "loading";
  useEffect(() => {
    if (draftOwner) setRewindUploading(draftOwner, attachmentsLoading);
  }, [draftOwner, attachmentsLoading]);
  const hasAttachments = pendingImages.length > 0 || pendingDocs.length > 0 || !!pendingRecording;
  const sendDisabled = fnFormActive
    ? false
    : attachmentBlock != null
      || pasteMissing.size > 0
      || (!input.trim() && !hasAttachments);
  const sendTitle = fnFormActive
    ? missingFnParams.length > 0
      ? text(
          `Fill required field${missingFnParams.length > 1 ? "s" : ""}: ${missingFnParams.join(", ")}`,
          `请填写必填字段：${missingFnParams.join(", ")}`,
        )
      : fnFormSubmitAction?.label ?? text("Run", "运行")
    : pasteMissing.size > 0
    ? text("Paste content lost. Remove the red chip and re-paste.", "粘贴内容已丢失。请移除红色标签后重新粘贴。")
    : attachmentBlock === "loading"
    ? text("Wait for attachments to finish reading", "请等待附件读取完成")
    : attachmentBlock === "error"
    ? text("Remove or replace the failed attachment", "请移除或重新添加失败的附件")
    : text("Send message", "发送消息");

  /* ---- Render -------------------------------------------------------- */

  // Controls cluster — permission / plus menu / tool chips on the
  // left; model texts + effort pill + context ring on the right.
  // Always rendered in the detached .controlsRow below the wrapper, in
  // every mode — opening a fn-form only grows the wrapper
  // above it, the controls row itself never moves or restyles.
  const controlsCluster = (
    <ControlsCluster
      bound={bound}
      plusMenuOpen={plusMenuOpen}
      setPlusMenuOpen={setPlusMenuOpen}
      onPickImages={onPickImages}
      onRecord={canRecord && recordingOn ? () => setRecordDialogOpen(true) : undefined}
      recordingActive={!!activeRecording}
      pendingImagesCount={pendingImages.length}
      pendingDocsCount={pendingDocs.length}
      webSearchEnabled={webSearchEnabled}
      webSearchOrigin={webSearchOrigin}
      toggleWebSearch={toggleWebSearch}
      fastEnabled={fastEnabled}
      fastSupported={fastSupported}
      fastHint={fastHint}
      toggleFast={toggleFast}
      unattended={unattended}
      toggleUnattended={toggleUnattended}
      sandboxEnabled={sandbox}
      sandboxAvailable={sandboxAvailable}
      sandboxReason={sandboxReason}
      toggleSandbox={toggleSandbox}
      chatAgent={chatAgent}
      chatModel={chatModel}
      noEnabledModels={noEnabledModels}
      thinking={thinking}
      thinkingOptions={thinkingOptions}
      setThinking={setThinking}
      thinkingMenuOpen={thinkingMenuOpen}
      setThinkingMenuOpen={setThinkingMenuOpen}
      thinkingTriggerRef={thinkingTriggerRef}
    />
  );

  return (
    <div
      ref={inputAreaRef}
      className={styles.inputArea}
      data-composer-input-area
      data-tucked={tuck.covering ? "" : undefined}
      onPointerDown={tuck.onAreaPointerDown}
      onPointerMove={tuck.onAreaPointerMove}
      onPointerLeave={tuck.onAreaPointerLeave}
    >
      {/* Drop overlay scoped to the chat main column (#chatArea) —
          covers the conversation surface but lets the sidebars stay
          interactive. ``dragActive`` is set by the window-level
          drag listeners in useComposerAttachments; the actual file
          handling lives there too. ``mainRect`` is recomputed on
          drag enter so the overlay tracks layout / window-resize
          changes between drags. */}
      {dragActive && typeof document !== "undefined"
        ? createPortal(
            <ScopedDropOverlay within={wrapperRef.current?.closest<HTMLElement>(DROP_PANE)} />,
            document.body,
          )
        : null}
      <div className={styles.decisionColumn}><PendingDecisionPanels sessionId={activeChatKey ?? currentSessionId} /></div>
      <TuckRow tucked={tuck.tucked} side="above" onSettled={tuck.onSettled}>
        <EnvironmentRow sessionId={currentSessionId} />
      </TuckRow>
      {/* Messages typed during the run dock directly above the input box. */}
      <QueuedMessages key={activeChatKey ?? currentSessionId ?? "new"} sessionId={activeChatKey ?? currentSessionId} />
      {/* composerStack wraps {slashClip, inputWrapper} so the slash
          menu's vertical anchor is the wrapper's top edge — not a
          magic-number offset from the inputArea bottom. composerStack
          is position:relative and naturally takes inputWrapper's
          height (slashClip is absolute, doesn't contribute), so
          slashClip's bottom:100% lands exactly at the wrapper top. */}
      <div className={styles.composerStack} data-composer-session={currentSessionId}>
      {/* The tuck band's outer half: a transparent ring behind the box
          (use-tucked-controls.ts). */}
      {tuck.enabled ? <div ref={tuck.bandRef} className={styles.tuckBand} style={{ borderRadius: `calc(var(--radius-xl) + ${RIM_OUT}px)` }} data-tuck-band aria-hidden="true" /> : null}
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
        className={`${styles.inputWrapper} ${morphed ? styles.morphed : ""}`}
        data-tuckable={tuck.enabled ? "" : undefined}
        data-voice={voice.state !== "idle" ? voice.state : undefined}
      >
        {pendingRecording ? (
          <RecordingTile
            info={pendingRecording}
            onRemove={() => useRecordingStore.getState().setPending(recordingComposer, null)}
          />
        ) : null}
        <AttachmentStrip
          sessionId={currentSessionId}
          pendingImages={pendingImages}
          pendingDocs={pendingDocs}
          imageError={imageError}
          fileInputRef={fileInputRef}
          onFileInputChange={onFileInputChange}
          onRemoveImage={removeImage}
          onReplaceImage={replaceImage}
          onRemoveDoc={removeDoc}
          onDismissError={() => setImageError(null)}
        />

        <ComposerBody
          bound={bound}
          composerMode={composerMode}
          fnFormFunction={fnFormFunction}
          fnForm={fnForm}
          handleFnFormClose={handleFnFormClose}
          submitFnForm={submitFnForm}
          outgoingFn={outgoingFn}
          textareaRef={textareaRef}
          input={input}
          setInput={setInput}
          isRunning={isRunning || awaitingAnswer}
          runningPlaceholder={awaitingAnswer ? text(
            "Reply to the request above, or tell the agent what to do instead",
            "答复上面的请求，或者告诉 agent 改做什么",
          ) : text(
            `Enter adds to this turn · ${modKey}Enter queues · Esc stops`,
            `回车插入当前轮 · ${modKey}回车排到下一轮 · Esc 停止`,
          )}
          idlePlaceholder={disconnected ? text(
            "Not connected. Messages wait and send once the connection is back.",
            "未连接。消息会排队，连接恢复后发送。",
          ) : undefined}
          draftOwner={draftOwner ?? undefined}
          suggestion={nextPrompt.suggestion}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          slash={slash}
          fileMention={fileMention}
        />

        {/* Voice input: mic in the bottom-left corner, live level strip
            before send in the bottom-right. */}
        {!fnFormActive ? <MicButton state={voice.state} onToggle={voice.toggle} /> : null}
        {!fnFormActive ? <VoiceStrip state={voice.state} levels={voice.levels} /> : null}

        {/* Chat and function-form actions stay independent of decision cards. */}
        <button
          className={`${cn(buttonVariants({
            variant: showStop ? "destructive" : sendDisabled ? "ghost" : "default",
            size: "icon-sm",
          }))} ${styles.actionBtn} ${showStop ? "" : styles.sendBtn}`}
          onClick={showStop ? stop : onSendButtonClick}
          disabled={isCancelling || (!showStop && sendDisabled)}
          data-fn-missing={
            !showStop && fnFormActive && missingFnParams.length > 0
              ? "true"
              : undefined
          }
          onMouseEnter={() => sendIconRef.current?.startAnimation?.()}
          onMouseLeave={() => sendIconRef.current?.stopAnimation?.()}
          title={
            isCancelling
              ? text("Cancelling…", "正在取消")
              : showStop
                ? text("Stop this reply (Esc)", "停止这次回复（Esc）")
                : sendTitle
          }
          aria-label={
            isCancelling
              ? text("Cancelling…", "正在取消")
              : showStop
                ? text("Cancel execution", "取消运行")
                : sendTitle
          }
          type="button"
        >
          {showStop ? <StopIcon /> : <SendIcon ref={sendIconRef} />}
        </button>

        {/* Function-form close stays in the header. */}
        {fnFormActive && !fnForm.closing && (
          <button
            className={styles.closeBtn}
            type="button"
            onClick={handleFnFormClose}
            onMouseDown={(e) => e.preventDefault()}
            tabIndex={-1}
            title={text("Close", "关闭")}
            aria-label={text("Close", "关闭")}
          >
            <SolarIcon name="close-circle" size={16} fit="fill" />
          </button>
        )}
      </div>
      </div>{/* /.composerStack */}
      <TuckRow tucked={tuck.tucked} side="below" onSettled={tuck.onSettled}>
        <div ref={controlsRowRef} className={`${styles.controlsRow} composer-bottom-row`}>
          {controlsCluster}
        </div>
      </TuckRow>
      {recordDialogOpen ? (
        <RecordDialog composer={recordingComposer} onClose={() => setRecordDialogOpen(false)} />
      ) : null}
    </div>
  );
}
