/**
 * Composer — chat input area.
 *
 * Owns: input value, attachments, slash menu, plus menu (Tools / Web
 * Search), thinking-effort selector, token badge, send/stop button.
 * Submits chat turns directly via the WS channel; no legacy globals.
 *
 * Styling lives in ./composer.module.css. The page-level chat layout
 * (chat-area, welcome screen, message list, etc.) is still rendered
 * by the legacy template for the moment and will be migrated in
 * subsequent slices.
 */
"use client";

import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { Menu } from "@base-ui-components/react/menu";
import { FileTextIcon } from "@/components/animated-icons";
import { HoverTip } from "@/components/ui/tooltip";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";

import { useSessionStore } from "@/lib/session-store";
import { api } from "@/lib/net/api";
import { showToast } from "@/lib/format-utils/toast";
import { useTranslation } from "@/lib/i18n";

import { ContextBadge } from "../context-badge";
// Session-scope chips relocated from the dismantled 48px topbar row —
// each carries its own popover menu (project-menu / agent-selector /
// permission-menu submodules under ../top-bar).
import { AgentBadge, PermissionBadge, ProjectBadge } from "../top-bar";
import { ChannelMenu } from "../top-bar/channel-menu";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { FunctionForm, visibleParams } from "./modes/fn-form/fn-form";
import { QuestionMode, type DecisionAction } from "./modes/question/question-mode";
import { resolveComposerMode } from "./modes/resolve-mode";
import {
  FastIcon,
  OptionsIcon,
  SendIcon,
  StopIcon,
  ToolsIcon,
  UnattendedIcon,
  WebSearchIcon,
} from "./icons";
import { type AnimatedNavIconHandle } from "@/components/animated-icons";
import { PlusMenuItem, ToolChip } from "./controls/menu-pieces";
import { type SlashCommand } from "./slash/slash-commands";
import { sendChatMessage } from "./legacy-send";
import {
  LONG_PASTE_THRESHOLD,
  expandPasteTokens,
  missingPasteIds,
  pasteStore,
  placeholderToken,
  referencedPasteIds,
} from "./paste/paste-store";
import { ChatInputRow } from "./chat-input-row";
import { SlashMenu } from "./slash/slash-menu";
import { collectImagesFromTransfer } from "./attach/image-attach";
import { expandAtMentions } from "./attach/at-mention";
import { FileTiles } from "./attach/file-tiles";
import { useComposerAttachments } from "./attach/use-composer-attachments";
import { useFileMention } from "./attach/use-file-mention";
import { ImageAttachStrip } from "./attach/image-attach-strip";
import { ThinkingEffortPill } from "./controls/thinking-effort-pill";
import { useFnFormState } from "./modes/fn-form/use-fn-form-state";
import { useFnFormWrapper } from "./modes/fn-form/use-fn-form-wrapper";
import { useSlashMenu } from "./slash/use-slash-menu";
import { useThinkingEffort } from "./controls/use-thinking-effort";
import { usePermissionMode } from "./controls/use-permission-mode";
import { useToolsToggles } from "./controls/use-tools-toggles";
import styles from "./composer.module.css";

/** Don't recall a user message longer than this through ↑/↓ history
 *  cycling. Long messages (full pasted code, expanded tokens, etc.)
 *  are not useful to step through and bloat the persisted draft on
 *  every keystroke once recalled. The user can still scroll back to
 *  the original message in the chat transcript to re-use it. */
const HISTORY_RECALL_MAX = 5000;

/* Single shared WebSocket. The legacy chat-ws.js script opens it as
   `window.ws`; this is the only point in the React layer that touches
   the global. When the WS layer is migrated (next slice), this helper
   is replaced by ``useWS().send`` and the call sites stay identical. */
function wsSend(payload: unknown): boolean {
  const w = window as Window & { ws?: WebSocket };
  if (!w.ws || w.ws.readyState !== WebSocket.OPEN) return false;
  w.ws.send(typeof payload === "string" ? payload : JSON.stringify(payload));
  return true;
}

const noop = () => {};

// `.plusMenu` was written for the old hand-rolled portal — it carries
// `position:absolute; bottom:100%; left:0; margin-bottom:4px` to sit
// above the trigger. base-ui's Menu positions the *Positioner* wrapper
// and we apply `.plusMenu` to the inner Popup panel, so those absolute
// props would fight the Positioner's transform. Neutralize them here
// (visuals — bg/border/radius/shadow/padding — stay untouched) so the
// menu reads identically while base-ui's Positioner owns placement,
// flip, and alignment (including the submenus' side="top").
const POPUP_STATIC_RESET: React.CSSProperties = {
  position: "static",
  bottom: "auto",
  left: "auto",
  marginBottom: 0,
};

export function Composer() {
  const { text } = useTranslation();
  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  // Per-session running state: send/stop button binds to the current
  // session's running task, not a global flag. This is what lets the
  // user switch from a running session A to session B and immediately
  // send a new message in B while A is still streaming.
  const runningTask = useSessionStore((s) =>
    s.currentSessionId ? (s.runningTasks[s.currentSessionId] ?? null) : null,
  );
  const input = useSessionStore((s) => s.composerInput);
  const setInput = useSessionStore((s) => s.setComposerInput);
  const focusTick = useSessionStore((s) => s.composerFocusTick);

  // History recall — user messages from the active session, ordered
  // oldest-first to match TUI semantics: ↑ steps backwards starting at
  // the newest, ↓ steps forward toward the live draft. Built from
  // ``messageOrder[currentSessionId]`` filtered to user role. Resets
  // automatically whenever the session changes via the useEffect below.
  const messagesById = useSessionStore((s) => s.messagesById);
  const messageOrder = useSessionStore((s) =>
    s.currentSessionId ? s.messageOrder[s.currentSessionId] : undefined,
  );
  const history = React.useMemo<string[]>(() => {
    if (!messageOrder) return [];
    const out: string[] = [];
    for (const id of messageOrder) {
      const m = messagesById[id];
      if (m && m.role === "user" && typeof m.content === "string"
          && m.content.trim()
          // Skip giant messages — recalling them into the textarea
          // would bloat the persisted draft (the per-keystroke write
          // to ``composerDrafts``) and is rarely useful: long messages
          // are typically expanded pastes, not commands the user wants
          // to step back through with ↑/↓.
          && m.content.length <= HISTORY_RECALL_MAX) {
        out.push(m.content);
      }
    }
    return out;
  }, [messageOrder, messagesById]);
  const [historyIndex, setHistoryIndex] = useState<number>(-1);
  // Reset history index when the session switches.
  useEffect(() => {
    setHistoryIndex(-1);
  }, [currentSessionId]);

  // Long-paste auto-attach. Subscribing to the store rerenders the chip
  // row whenever a paste is added or removed. The store itself is a
  // module-level singleton (process-wide) so a paste survives session
  // switches and is referenced by id from the live composer text.
  const [pasteTick, setPasteTick] = useState(0);
  useEffect(() => pasteStore.subscribe(() => setPasteTick((t) => t + 1)), []);
  const pastedEntries = React.useMemo(() => {
    const referenced = referencedPasteIds(input);
    // Only show chips for tokens still present in the live draft.
    // Include lost ones (chips in "missing" state) so the user sees
    // them and can remove the dead tokens.
    const live = pasteStore.list().filter((e) => referenced.has(e.id));
    const liveIds = new Set(live.map((e) => e.id));
    const out = [...live];
    referenced.forEach((id) => {
      if (!liveIds.has(id)) {
        out.push({ id, content: "", numLines: 0 });
      }
    });
    return out.sort((a, b) => a.id - b.id);
    // pasteTick is read implicitly by re-running this memo on tick bump.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input, pasteTick]);
  const pasteMissing = React.useMemo(
    () => missingPasteIds(input),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [input, pasteTick],
  );

  // GC paste store entries that no draft references anymore. Watches
  // composerDrafts AND the live ``input`` (the current session's
  // draft). Without this, removing a session leaves its pastes stuck
  // in the store forever.
  const composerDrafts = useSessionStore((s) => s.composerDrafts);
  useEffect(() => {
    const all = new Set<number>();
    referencedPasteIds(input).forEach((id) => all.add(id));
    for (const k in composerDrafts) {
      referencedPasteIds(composerDrafts[k]).forEach((id) => all.add(id));
    }
    pasteStore.retainOnly(all);
  }, [composerDrafts, input]);

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
    addImages,
    removeImage,
    setImageError,
    removeDoc,
    onPickImages,
    onFileInputChange,
    clearAfterSubmit: clearAttachmentsAfterSubmit,
  } = useComposerAttachments();

  // Refs declared up-front so the @file mention hook below can
  // reference them. The other refs (wrapper, sendBtn, plus menu,
  // thinking pill) get declared in their original spot.
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const onPaste = useCallback(
    (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      // Image clipboard items take priority — if the user copied a
      // screenshot we want to attach it, never paste raw binary into
      // the textarea. Browsers populate ``items[].kind === "file"``
      // for image pastes from screenshot tools and most file
      // managers; ordinary text pastes leave items empty / "string".
      const items = e.clipboardData?.items;
      if (items) {
        let hasImage = false;
        for (let i = 0; i < items.length; i++) {
          if (items[i].kind === "file"
              && items[i].type.startsWith("image/")) {
            hasImage = true;
            break;
          }
        }
        if (hasImage) {
          e.preventDefault();
          void collectImagesFromTransfer(e.clipboardData!)
            .then((imgs) => addImages(imgs))
            .catch((err) => setImageError(String(err)));
          return;
        }
      }
      const text = e.clipboardData?.getData("text") ?? "";
      if (text.length < LONG_PASTE_THRESHOLD) return;
      // Replace the textarea selection with our placeholder token and
      // stash the real content in the paste store.
      e.preventDefault();
      const entry = pasteStore.add(text);
      const token = placeholderToken(entry);
      const ta = e.currentTarget;
      const start = ta.selectionStart ?? input.length;
      const end = ta.selectionEnd ?? start;
      const next = input.slice(0, start) + token + input.slice(end);
      setInput(next);
      // Place caret after the inserted token on the next frame.
      requestAnimationFrame(() => {
        const pos = start + token.length;
        ta.setSelectionRange(pos, pos);
      });
    },
    [input, setInput, addImages, setImageError],
  );

  // Remove a paste chip — also strips the token from the textarea.
  const removePaste = useCallback(
    (id: number) => {
      const re = new RegExp(`\\[Pasted #${id} \\+\\d+ lines\\]`, "g");
      setInput(input.replace(re, ""));
      pasteStore.remove(id);
    },
    [input, setInput],
  );

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
  const activeDecision =
    pendingDecisions.find((d) => d.sessionId === currentSessionId) ?? null;
  const router = useRouter();
  const send = wsSend;

  const isRunning = runningTask !== null;
  const fnFormActive = fnFormFunction !== null;
  // 右下角圆按钮是否该显示红色停止 ■：仅当任务真在跑、且当前没有 decision
  // 占据输入区。decision 在场时函数虽“运行”着，但它在等用户答题——此刻这个
  // 按钮要当“提交”用，不能变停止键（否则点了是中断函数，不是交答案）。
  const showStop = isRunning && activeDecision === null;

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
    if (activeDecision && fnFormFunction) {
      closeFnFormStore();
    }
  }, [activeDecision, fnFormFunction, closeFnFormStore]);

  // @file mention — state + debounced /api/file-search + popover
  // positioning + picker all live in ./use-file-mention now. The hook
  // owns the 6 useStates + 2 effects + pickFile callback that used to
  // sit here.
  const {
    atToken,
    setCaretPos,
    fileMatches,
    fileMenuIndex,
    setFileMenuIndex,
    fileMenuLoading,
    fileMenuPos,
    pickFile,
    closeMenu: closeFileMenu,
  } = useFileMention({ input, setInput, textareaRef });


  // Thinking-effort + plus-menu + tools toggles each live in their own
  // dedicated hooks now — see ./use-thinking-effort, ./use-tools-toggles.
  const {
    thinking,
    options: thinkingOptions,
    menuOpen: thinkingMenuOpen,
    setMenuOpen: setThinkingMenuOpen,
    set: setThinking,
  } = useThinkingEffort();
  // Permission mode is now owned by the top-bar <PermissionBadge> chip;
  // the composer only needs the current value to tag the outgoing turn's
  // ``permission_mode`` (submit fallback). Reading the same per-session
  // store means the chip's switch is reflected here immediately.
  const { mode: permMode } = usePermissionMode();
  // The effort picker only appears once a chat model is actually
  // selected; with no model picked it stays hidden.
  const chatModel = useSessionStore((s) => s.agentSettings?.chat?.model);
  // Agent settings feed the relocated chat/exec model chips below —
  // same store slice the old topbar row read (populated by the
  // window-bridge wrappers via <LegacyTopbarBridge />).
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
  // per-session (store's composerSettings.fast, persisted + isolated per
  // chat like the other toggles). The backend forwards it to the provider
  // request body and no-ops for providers that don't read service_tier.
  const fastEnabled = useSessionStore((s) => s.composerSettings.fast);
  // 有的模型没有 Fast 档（service_tier）——后端 agent_settings 按当前
  // 模型下发 chat.fast；不支持就整个隐藏开关/chip，也不随消息发送。
  const fastSupported = useSessionStore((s) => !!s.agentSettings?.chat?.fast);
  const setComposerSettings = useSessionStore((s) => s.setComposerSettings);
  const toggleFast = () =>
    setComposerSettings({ fast: !useSessionStore.getState().composerSettings.fast });
  // Unattended toggle: nobody watching → withhold the agent's user-question
  // tool. Mirror the per-session UI flag to the backend via set_attended so
  // the tool-resolution gate matches (attended = !unattended).
  const unattended = useSessionStore((s) => s.composerSettings.unattended);
  const toggleUnattended = () => {
    const next = !useSessionStore.getState().composerSettings.unattended;
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
  // outside (it ignores the expanded prop and only collapses on host
  // mouseleave).
  const [effortEpoch, setEffortEpoch] = useState(0);
  const plusIconRef = useRef<AnimatedNavIconHandle>(null);

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
        // The floating slider (detached row) collapses on host
        // mouseleave — a pointer that opened it via the text trigger
        // and clicked elsewhere without touching the slider would
        // leave it stuck open. Remount the pill to reset it.
        if (thinkingTriggerRef.current.querySelector("[data-effort-expanded]")) {
          setEffortEpoch((e) => e + 1);
        }
      }
    }
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, [setThinkingMenuOpen]);

  // /context 面板开关放 store —— badge（右下角圆环）负责渲染浮动弹窗，
  // /context slash 命令只需把它打开，弹窗即锚定圆环向上展开。
  const openContextPanel = useSessionStore((s) => s.setContextPanelOpen);

  // Slash menu (state + open/close timing + command dispatch).
  const slash = useSlashMenu({
    input,
    textareaRef,
    send,
    openContextPanel: () => openContextPanel(true),
  });

  /* ---- Submit -------------------------------------------------------- */

  const submit = useCallback(async () => {
    const trimmed = input.trim();
    // While a task is running, a sent message is a MID-RUN STEER, not a new
    // turn: route it to the live run so the user can course-correct just by
    // typing into the same box. The running loop picks it up at its next step
    // boundary. (Plain text only — attachments / slash go through the normal
    // path, which is disabled while running.)
    if (isRunning) {
      if (!trimmed || !currentSessionId) return;
      send({ action: "steer", session_id: currentSessionId, message: trimmed });
      setInput("");
      return;
    }
    // Allow image-only submits — the LLM can answer "describe this
    // screenshot" without text. Otherwise require at least one of
    // text or attached image.
    if (!trimmed && pendingImages.length === 0 && pendingDocs.length === 0) {
      return;
    }
    // No enabled model → don't send. Routing a turn with nothing
    // enabled would silently run on a pinned default (the user disabled
    // everything on purpose). Point them at the top-bar picker instead.
    if (noEnabledModels) {
      promptNeedModel();
      return;
    }
    // Block submit while any attachment is still being decoded — the
    // placeholder chips have empty ``attachment.data`` / null
    // ``content``, which would deliver broken payloads. The user
    // sees the chips in a loading shimmer; they just need to wait.
    if (pendingImages.some((p) => p.loading)
        || pendingDocs.some((d) => d.loading)) {
      return;
    }
    if (slash.query !== null && slash.runCommand(trimmed)) {
      setInput("");
      slash.close();
      return;
    }
    // Block submit if any paste token in the draft has lost its
    // backing content. The chip row renders these in red and the
    // ``sendDisabled`` guard below also disables the send button, but
    // re-check here so an Enter-key submit can't slip through if the
    // chip refresh hadn't fired yet.
    if (missingPasteIds(trimmed).size > 0) return;
    // Expand long-paste tokens (``[Pasted #N +M lines]``) back into
    // the outgoing text so the LLM receives the real content. The
    // entries stay in the store — they're now GC'd by the
    // composerDrafts effect once no draft references them anymore.
    let expanded = expandPasteTokens(trimmed);
    // Then expand any ``@path`` mentions by reading the files via the
    // worker's HTTP API. Mentions that fail to read stay as the
    // original ``@path`` token (no silent data loss).
    try {
      const mentionResult = await expandAtMentions(expanded, null);
      expanded = mentionResult.text;
    } catch {
      /* network blip — fall through with raw text */
    }
    // Attached docs are referenced by PATH, never inlined. Each one's
    // bytes ride along as a ``type:"document"`` attachment; the backend
    // saves it under the session workdir and appends " @ <abs path>" to
    // the mention (see ws_actions/chat.py). We emit the path-less
    // ``[attachment: …]`` mention here so the optimistic bubble + chip
    // parser have something to show before the server-side save lands.
    // (Docs without captured bytes — over the 25 MB cap — are dropped:
    // there's no way to ship them, and a mention with no backing file
    // the agent can read would only mislead it.)
    if (pendingDocs.length > 0) {
      // Emit a mention for EVERY doc so none vanishes silently. Docs with
      // captured bytes get the normal path-less mention (backend appends
      // the @path + page/line count). Docs that couldn't be read (over the
      // size cap → dataB64 null) get an honest "too large" note instead of
      // being dropped — the chip still shows, the model is told.
      const mentions = pendingDocs.map((d) => {
        const meta = `${d.ext || "file"}, ${Math.max(1, Math.round(d.sizeBytes / 1024))} KB`;
        return d.dataB64
          ? `[attachment: ${d.filename} (${meta})]`
          : `[attachment: ${d.filename} (${meta}, too large — not sent)]`;
      });
      if (mentions.length > 0) {
        expanded = `${mentions.join("\n")}\n\n${expanded}`;
      }
    }
    const imagesPayload = pendingImages.map((p) => p.attachment);
    const docsPayload = pendingDocs
      .filter((d) => d.dataB64)
      .map((d) => ({
        type: "document" as const,
        data: d.dataB64 as string,
        media_type: d.mediaType || "application/octet-stream",
        filename: d.filename,
      }));
    const attachmentsPayload = [...imagesPayload, ...docsPayload];
    // Delegate to legacy `sendMessage` (chat.js) so the user bubble +
    // welcome-hide + assistant placeholder + isRunning flip all fire
    // before the WS payload goes out. Composer is just the trigger.
    const handled = sendChatMessage({
      text: expanded,
      attachments: attachmentsPayload.length > 0 ? attachmentsPayload : undefined,
      thinking,
      toolsEnabled,
      webSearchEnabled,
      activeProfile,
      serviceTier: fastEnabled && fastSupported ? "priority" : undefined,
    });
    if (!handled) {
      // chat.js hasn't loaded yet (shouldn't happen in steady state).
      // Fall back to a raw send so we don't lose the user's text; the
      // welcome-screen / user-bubble update is out of scope here.
      const ok = send({
        action: "chat",
        text: expanded,
        session_id: currentSessionId ?? null,
        thinking_effort: thinking,
        tools: toolsEnabled,
        web_search: webSearchEnabled,
        ...(permMode ? { permission_mode: permMode } : {}),
        ...(fastEnabled && fastSupported ? { service_tier: "priority" } : {}),
      });
      if (!ok) return;
    }
    setInput("");
    setHistoryIndex(-1);
    // Revoke + clear pending images / docs now that the WS payload
    // is out the door. Hook handles URL.revokeObjectURL for each
    // image's preview blob.
    clearAttachmentsAfterSubmit();
    slash.close();
  }, [
    clearAttachmentsAfterSubmit,
    currentSessionId,
    input,
    isRunning,
    noEnabledModels,
    pendingDocs,
    pendingImages,
    promptNeedModel,
    send,
    setInput,
    slash,
    thinking,
    toolsEnabled,
    webSearchEnabled,
    fastEnabled,
    fastSupported,
  ]);

  function stop() {
    if (!currentSessionId) return;
    // Optimistic UI flip: clear runningTask immediately so the Stop
    // button turns back into Send right when the user clicks. Don't
    // wait for the backend's stopped envelope — the dispatcher main
    // thread can be blocked for several seconds on an in-flight LLM
    // stream while cancel propagates. The actual backend cleanup
    // still runs (subprocess SIGKILL is instant; cancel hook reaches
    // a hook point within ~1s for the chat path), it just no longer
    // gates the UI.
    const store = useSessionStore.getState();
    store.setRunningTaskFor(currentSessionId, null);
    // Also patch the running assistant placeholder (the row that
    // would otherwise be filled in 5-6s later with the late-arriving
    // LLM response) to a cancelled state right now. Backend's
    // dispatcher will overwrite the persisted node with the same
    // ``[cancelled by user]`` content + status=cancelled when its
    // cancel-aware finalize runs, so the React store and the on-disk
    // node converge. Without this the chat would show a Thinking
    // spinner until the model's stream completed naturally.
    const ids = store.messageOrder[currentSessionId] || [];
    for (let i = ids.length - 1; i >= 0; i--) {
      const m = store.messagesById[ids[i]];
      if (!m) continue;
      if (m.role !== "assistant") continue;
      if (m.status === "done" || m.status === "completed"
          || m.status === "cancelled" || m.status === "error") break;
      store.updateMessage(currentSessionId, m.id, {
        status: "cancelled",
        content: m.content && m.content.trim()
          ? `${m.content}\n\n*[cancelled by user]*`
          : "*[cancelled by user]*",
        thinking: undefined,
      });
      break;
    }
    send({ action: "stop", session_id: currentSessionId });
  }

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

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Don't hijack Enter while an IME is composing — the user is
    // confirming a Chinese / Japanese / Korean candidate, not
    // sending. ``isComposing`` is set during the IME session;
    // Chromium also reports keyCode 229 for the same window.
    // Reading off ``nativeEvent`` because React's synthetic event
    // type doesn't include the flag yet.
    const native = e.nativeEvent as KeyboardEvent;
    if (native.isComposing || native.keyCode === 229) {
      return;
    }
    // @file mention menu takes precedence — its arrows / enter / esc /
    // tab steer the menu, never fall through to history-recall or
    // the slash menu.
    if (atToken && fileMatches.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFileMenuIndex((i) => Math.min(fileMatches.length - 1, i + 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setFileMenuIndex((i) => Math.max(0, i - 1));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const picked = fileMatches[fileMenuIndex] ?? fileMatches[0];
        if (picked) pickFile(picked);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closeFileMenu();
        return;
      }
    }
    // Fish-shell-style history recall. Mirrors the TUI's PromptInput
    // logic (cli/src/components/PromptInput/PromptInput.tsx). Only fires
    // when the slash menu isn't holding the arrows and the caret is on
    // the first / last visual line of the textarea, so multi-line
    // editing still works naturally.
    if (e.key === "ArrowUp" && !slash.visible && !e.shiftKey
        && !e.metaKey && !e.altKey) {
      const ta = e.currentTarget;
      // Enter recall mode when caret is on the first visual line and
      // nothing is selected. Once recall mode is active (historyIndex
      // >= 0) ↑ keeps stepping back regardless of caret position.
      const firstNewline = input.indexOf("\n");
      const onFirstLine = ta.selectionStart === ta.selectionEnd
        && ta.selectionStart <= (firstNewline < 0 ? input.length : firstNewline);
      if (history.length > 0 && (historyIndex >= 0 || onFirstLine)) {
        e.preventDefault();
        const next = historyIndex < 0
          ? history.length - 1
          : Math.max(0, historyIndex - 1);
        setHistoryIndex(next);
        setInput(history[next] ?? "");
        // Move caret to end so the next ↑ keeps recalling instead of
        // moving inside the freshly-loaded text.
        requestAnimationFrame(() => {
          const v = history[next] ?? "";
          ta.setSelectionRange(v.length, v.length);
        });
        return;
      }
    }
    if (e.key === "ArrowDown" && !slash.visible && historyIndex >= 0
        && !e.shiftKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      const next = historyIndex + 1;
      if (next >= history.length) {
        setHistoryIndex(-1);
        setInput("");
      } else {
        setHistoryIndex(next);
        setInput(history[next] ?? "");
      }
      return;
    }
    // Any typing (non-arrow key) drops out of history-recall mode so
    // editing a recalled entry doesn't re-snap when the user hits ↑
    // again.
    if (historyIndex >= 0 && e.key.length === 1) {
      setHistoryIndex(-1);
    }
    // While the slash menu is open it captures the arrow keys (move the
    // highlight), Enter (pick the highlighted command) and Escape.
    if (slash.visible) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        slash.move(1);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        slash.move(-1);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        slash.close();
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        // activeIndex starts at -1 (no kbd nav yet); fall back to the
        // first match so ``/sp<Enter>`` runs ``/spawn`` without the
        // user having to press ArrowDown first.
        const idx = slash.activeIndex >= 0 ? slash.activeIndex : 0;
        const cmd = slash.matches[idx];
        if (cmd) selectSlashCommand(cmd);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  }

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

  const submitFnForm = useCallback(() => {
    if (!fnFormFunction || isRunning) return;
    // Same gate as chat: a function run needs a model to dispatch
    // against. With nothing enabled, prompt for one instead of letting
    // the agent run on a pinned default.
    if (noEnabledModels) {
      promptNeedModel();
      return;
    }
    const fn = fnFormFunction;
    const workdirMode = fn.workdir_mode ?? "optional";
    const wd = fnForm.workdir.trim();
    if (workdirMode === "required" && !wd) {
      fnForm.setError("__workdir");
      return;
    }

    // Build typed kwargs for the new POST /api/function/{name} endpoint.
    // Track A removed the /run text-command path entirely — fn-form
    // submits now talk to the dispatcher's forced tool-call entry instead
    // of round-tripping through the chat WS as `run name k=v ...` text.
    const kwargs: Record<string, unknown> = {};
    for (const p of visibleParams(fn)) {
      const isBool = p.type === "bool" || p.type === "boolean";
      const isInt = p.type === "int";
      const isFloat = p.type === "float" || p.type === "number";
      let v = String(fnForm.values[p.name] ?? "").trim();
      if (!v && isBool) v = "False";
      if (!v && !p.required) continue;
      if (!v && p.required) {
        fnForm.setError(p.name);
        return;
      }
      if (isBool) {
        kwargs[p.name] = v === "True" || v === "true" || v === "1";
      } else if (isInt) {
        const n = parseInt(v, 10);
        kwargs[p.name] = Number.isFinite(n) ? n : v;
      } else if (isFloat) {
        const n = parseFloat(v);
        kwargs[p.name] = Number.isFinite(n) ? n : v;
      } else {
        kwargs[p.name] = v;
      }
    }

    const body: Record<string, unknown> = { kwargs };
    if (workdirMode !== "hidden" && wd) body.work_dir = wd;
    if (currentSessionId) body.session_id = currentSessionId;
    // "修改后重新运行"：以原调用为锚点 fork 兄弟分支（旧运行保留在
    // ◀ N/M ▶ 切换里），不是在对话尾部追加一次新调用。
    const forkOf = useSessionStore.getState().fnFormForkOf;
    if (forkOf) body.fork_of_node = forkOf;

    // Hide welcome panel right away (matches old sendChatMessage UX).
    const w = window as unknown as {
      setWelcomeVisible?: (show: boolean) => void;
      setRunning?: (running: boolean) => void;
    };
    w.setWelcomeVisible?.(false);
    w.setRunning?.(true);

    // 0ms feedback (interaction-feedback policy): drop a client-side
    // pending runtime card into the transcript right now so the user sees
    // the function start instead of a blank gap until the ~0.13s hydrate.
    // The dispatcher pre-creates the run's node and a load_session
    // hydrate (chat_ack {function_run:true}) replaces the whole transcript
    // — that setMessages wipes this placeholder's id, so the real card
    // takes its place with no flicker. Only when we already have a session
    // to key it under; a brand-new session's card lands via the post-POST
    // navigate + hydrate.
    let placeholderId: string | null = null;
    if (currentSessionId) {
      const store = useSessionStore.getState();
      placeholderId = `__optimistic_fn__:${fn.name}:${Date.now()}`;
      store.appendMessage(currentSessionId, {
        id: placeholderId,
        role: "assistant",
        content: "",
        display: "runtime",
        function: fn.name,
        status: "running",
      });
      store.setRunningTaskFor(currentSessionId, {
        session_id: currentSessionId,
        msg_id: placeholderId,
        func_name: fn.name,
        started_at: Date.now() / 1000,
      });
    }

    void fetch(`/api/function/${encodeURIComponent(fn.name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(async (r) => {
        const j = await r.json().catch(() => null);
        if (!r.ok) {
          const msg =
            j && typeof j.error === "string"
              ? j.error
              : `HTTP ${r.status}`;
          throw new Error(msg);
        }
        // POST returns {session_id, msg_id}. If we weren't already
        // bound to a session, navigate to /s/<sid> + flip the store's
        // currentSessionId — without this the runtime placeholder
        // stream-resumes into a session the chat area can't see, and
        // the page stays blank while gui_agent runs in the background.
        const sid = j && j.session_id;
        if (typeof sid === "string" && sid) {
          (window as unknown as { currentSessionId?: string }).currentSessionId =
            sid;
          if (sid !== currentSessionId) {
            setCurrentConv(sid);
            router.push(`/s/${encodeURIComponent(sid)}`);
          }
        }
      })
      .catch((err) => {
        console.error("function call failed:", err);
        w.setRunning?.(false);
        // Roll back the optimistic pending card + running task — the
        // dispatch never landed, so leaving them would show a card
        // spinning forever with no backing run.
        if (placeholderId && currentSessionId) {
          const store = useSessionStore.getState();
          store.truncateFrom(currentSessionId, placeholderId);
          store.setRunningTaskFor(currentSessionId, null);
        }
        // Surface the reason to the user. The backend now returns a
        // structured 400 when a non-agentic tool is invoked via
        // fn-form, so without this the only feedback was a silent
        // console line — the chat panel showed nothing.
        const msg = err instanceof Error ? err.message : String(err);
        showToast(
          text(`Function call failed: ${msg}`, `函数调用失败：${msg}`),
          { tone: "error" },
        );
      });
    handleFnFormClose();
  }, [
    currentSessionId,
    fnFormFunction,
    fnForm,
    handleFnFormClose,
    isRunning,
    noEnabledModels,
    promptNeedModel,
    router,
    setCurrentConv,
  ]);

  // decision 在场时右下角是 mode 自己的 navButtons 按钮组（见 JSX），不走
  // 这个圆形按钮，所以这里只管 fn-form / 普通聊天两种。
  const onSendButtonClick = fnFormActive ? submitFnForm : submit;

  // 拒绝/取消当前的系统决定 —— 走左上角 ✕。发 question_reject 并即时出队
  // （后端 _resolve_question 收口 + 广播）。
  const rejectDecision = useCallback(() => {
    const d = activeDecision;
    if (!d) return;
    const w = window as unknown as { ws?: WebSocket };
    if (w.ws && w.ws.readyState === WebSocket.OPEN) {
      w.ws.send(JSON.stringify({ action: "question_reject", id: d.id }));
    }
    dequeueDecision(d.id);
  }, [activeDecision, dequeueDecision]);
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

  const anyToolActive =
    toolsEnabled || webSearchEnabled || (fastEnabled && fastSupported) || unattended;

  // Controls cluster — permission / plus menu / tool chips on the
  // left; model texts + effort pill + context ring on the right.
  // Rendered in exactly ONE of two containers per mode: the detached
  // .controlsRow below the wrapper (chat mode) or the legacy internal
  // .inputBottomRow (fn-form / question / approval).
  const controlsCluster = (
    <>
          <div className={styles.inputOptions}>
            {/* Permission control leads the left cluster, restyled by
                the wrapper CSS into Claude's borderless "Accept edits ⌄"
                text form (no border / bg; popover + id untouched). */}
            <PermissionBadge />
            <Menu.Root
              open={plusMenuOpen}
              onOpenChange={(o) => {
                setPlusMenuOpen(o);
                // Opening the plus menu collapses the effort pill (they
                // shared the bottom row and shouldn't be open at once).
                if (o) setThinkingMenuOpen(false);
              }}
            >
              <Menu.Trigger
                render={
                  <button
                    className={`${styles.plusBtn} ${anyToolActive ? styles.hasActive : ""}`}
                    onMouseEnter={() => plusIconRef.current?.startAnimation?.()}
                    onMouseLeave={() => plusIconRef.current?.stopAnimation?.()}
                    title={text("Add tools, files, and more", "添加工具、文件等")}
                    aria-label={text("More options", "更多选项")}
                    type="button"
                  >
                    <OptionsIcon ref={plusIconRef} />
                  </button>
                }
              />

              <Menu.Portal>
                {/* Positioner owns placement (side/align/offset + flip);
                    Popup is the actual panel that wears `.plusMenu`. The
                    static reset stops the old absolute props from fighting
                    the Positioner. */}
                <Menu.Positioner side="top" align="start" sideOffset={10} style={{ zIndex: 200 }}>
                  <Menu.Popup
                    className={styles.plusMenu}
                    style={POPUP_STATIC_RESET}
                  >
                    {/* Attach file — a plain action; clicking it closes the
                        menu (default Menu.Item closeOnClick behaviour). */}
                    <Menu.Item className={styles.plusMenuRow} onClick={() => onPickImages()}>
                      <PlusMenuItem
                        active={pendingImages.length > 0 || pendingDocs.length > 0}
                        onClick={noop}
                        icon={<FileTextIcon size={20} />}
                        label={text("Attach file", "添加照片和文件")}
                      />
                    </Menu.Item>

                    <Menu.Separator className={styles.plusMenuDivider} />

                    {/* Tools — a toggle row that ALSO opens a cascading
                        "Tool Profile" submenu. base-ui SubmenuRoot owns the
                        hover-open + flip; the submenu's Positioner uses
                        side="top" so it flies UP (space permitting, else it
                        flips). The row click toggles tools (via PlusMenuItem's
                        onClick, which bubbles) — the SubmenuTrigger itself
                        doesn't close the outer menu. */}
                    <Menu.SubmenuRoot>
                      <Menu.SubmenuTrigger className={styles.plusMenuRow}>
                        <PlusMenuItem
                          active={toolsEnabled}
                          onClick={toggleTools}
                          icon={<ToolsIcon />}
                          label={text("Tools", "工具")}
                        />
                      </Menu.SubmenuTrigger>
                      <Menu.Portal>
                        <Menu.Positioner side="right" align="end" sideOffset={6} style={{ zIndex: 200 }}>
                          <Menu.Popup
                            className={styles.plusMenu}
                            style={POPUP_STATIC_RESET}
                          >
                            <div style={{ padding: "4px 8px", fontSize: "11px",
                              color: "var(--text-muted)", textTransform: "uppercase",
                              letterSpacing: "0.05em" }}>
                              {text("Tool Profile", "工具配置")}
                            </div>
                            {Object.keys(toolProfiles).sort().map((pName) => (
                              <Menu.Item
                                key={pName}
                                className={styles.plusMenuRow}
                                onClick={() => switchProfile(pName)}
                              >
                                <PlusMenuItem
                                  active={activeProfile === pName}
                                  onClick={noop}
                                  icon={null}
                                  label={pName === "full"
                                    ? text("All Tools", "全部工具")
                                    : pName}
                                />
                              </Menu.Item>
                            ))}
                          </Menu.Popup>
                        </Menu.Positioner>
                      </Menu.Portal>
                    </Menu.SubmenuRoot>

                    {/* Web Search / Fast — toggles that must NOT close the
                        menu, so closeOnClick={false}. */}
                    <Menu.Item
                      className={styles.plusMenuRow}
                      closeOnClick={false}
                      onClick={() => toggleWebSearch()}
                    >
                      <PlusMenuItem
                        active={webSearchEnabled}
                        onClick={noop}
                        icon={<WebSearchIcon />}
                        label={text("Web Search", "网页搜索")}
                      />
                    </Menu.Item>
                    {fastSupported ? (
                      <Menu.Item
                        className={styles.plusMenuRow}
                        closeOnClick={false}
                        onClick={() => toggleFast()}
                      >
                        <PlusMenuItem
                          active={fastEnabled}
                          onClick={noop}
                          icon={<FastIcon />}
                          label={text("Fast", "高速")}
                        />
                      </Menu.Item>
                    ) : null}

                    <Menu.Separator className={styles.plusMenuDivider} />

                    {/* Unattended — a toggle; keep the menu open. */}
                    <Menu.Item
                      className={styles.plusMenuRow}
                      closeOnClick={false}
                      onClick={() => toggleUnattended()}
                    >
                      <PlusMenuItem
                        active={unattended}
                        onClick={noop}
                        icon={<UnattendedIcon />}
                        label={text("Unattended", "无人值守")}
                      />
                    </Menu.Item>
                  </Menu.Popup>
                </Menu.Positioner>
              </Menu.Portal>
            </Menu.Root>

            <div className={styles.activeToolChips}>
              {/* Only ENABLED tools show as a chip here. The off ones are
                  not rendered at all — they live in the + menu and are
                  turned on from there. An active chip shows its × on hover
                  to switch it back off. (The container is :empty →
                  display:none, so all-off shows nothing.) HoverTip is a
                  real top-layer tooltip; a CSS ::after would be cropped by
                  the chip's overflow:hidden. */}
              {toolsEnabled && (
                <HoverTip label={text("Tools", "工具")}>
                  <ToolChip
                    icon={<ToolsIcon size={16} />}
                    label={text("Tools", "工具")}
                    on
                    onToggle={toggleTools}
                  />
                </HoverTip>
              )}
              {webSearchEnabled && (
                <HoverTip label={text("Web Search", "网页搜索")}>
                  <ToolChip
                    icon={<WebSearchIcon size={16} />}
                    label={text("Web Search", "网页搜索")}
                    on
                    onToggle={toggleWebSearch}
                  />
                </HoverTip>
              )}
              {fastEnabled && fastSupported && (
                <HoverTip label={text("Fast", "高速")}>
                  <ToolChip
                    icon={<FastIcon size={16} />}
                    label={text("Fast", "高速")}
                    on
                    onToggle={toggleFast}
                  />
                </HoverTip>
              )}
              {unattended && (
                <HoverTip label={text("Unattended", "无人值守")}>
                  <ToolChip
                    icon={<UnattendedIcon size={16} />}
                    label={text("Unattended", "无人值守")}
                    on
                    onToggle={toggleUnattended}
                  />
                </HoverTip>
              )}
            </div>

          </div>
          <div className={styles.inputBottomRight}>
            {/* Claude-style right cluster before the send affordance:
                chat + exec models as quiet borderless text ("Opus 4.8"
                form, restyled via .agentChips overrides — components
                and their popovers untouched), effort pill, context
                ring last. The chatAgentBadge / execAgentBadge ids must
                survive — the window-bridge looks elements up by id. */}
            <div className={styles.agentChips}>
              <AgentBadge
                id="chatAgentBadge"
                kind="chat"
                locked={!!chatAgent.locked}
                provider={chatAgent.provider}
                model={chatAgent.model}
              />
              <AgentBadge
                id="execAgentBadge"
                kind="exec"
                locked={false}
                provider={execAgent.provider}
                model={execAgent.model}
              />
            </div>
            {/* Effort picker only when a chat model is selected. No
                persistent "no model" indicator here by design — a
                blocked send/run fires a transient top toast instead
                (see ``promptNeedModel``). The `thinking` value still
                flows to submit (uses the model default) when hidden. */}
            {chatModel && !noEnabledModels ? (
              <HoverTip label={text("Thinking effort", "思考力度")}>
                {/* Wrapper is the outside-click boundary AND the anchor
                    for the pill's floating slider (detached row). The
                    text trigger only shows in the detached row (CSS);
                    the morphed internal band keeps the icon pill. */}
                <div ref={thinkingTriggerRef} className={styles.effortControl}>
                  {thinkingOptions.length > 1 && (
                    <button
                      type="button"
                      className={styles.effortText}
                      // ponytail: the pill ignores its expanded/onToggle
                      // props (internal useState) — a programmatic click
                      // on its own (hidden) collapsed chip is the only
                      // public "open". Lift the state into the pill if a
                      // second caller ever needs it.
                      onClick={() => {
                        setPlusMenuOpen(false);
                        thinkingTriggerRef.current
                          ?.querySelector<HTMLElement>(".effort-pill-collapsed")
                          ?.click();
                      }}
                    >
                      {thinking ? thinking[0].toUpperCase() + thinking.slice(1) : ""}
                    </button>
                  )}
                  <ThinkingEffortPill
                    // Remount on epoch bump = force-collapse (see the
                    // outside-click handler): the pill only collapses on
                    // host mouseleave, which never fires if the pointer
                    // opened it from the external text trigger and then
                    // clicked elsewhere without touching the slider.
                    key={effortEpoch}
                    expanded={thinkingMenuOpen}
                    onToggle={() => {
                      setThinkingMenuOpen((v) => !v);
                      setPlusMenuOpen(false);
                    }}
                    options={thinkingOptions}
                    value={thinking}
                    onChange={setThinking}
                  />
                </div>
              </HoverTip>
            ) : null}
            <ContextBadge />
          </div>
    </>
  );

  return (
    <div className={styles.inputArea}>
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
        <StatusChip />
        <ProjectBadge />
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
        className={`${styles.inputWrapper} ${morphed ? styles.fnFormMode : ""}`}
      >
        <ImageAttachStrip
          pendingImages={pendingImages}
          imageError={imageError}
          fileInputRef={fileInputRef}
          onFileInputChange={onFileInputChange}
          onRemove={removeImage}
          onDismissError={() => setImageError(null)}
        />
        <FileTiles docs={pendingDocs} onRemove={removeDoc} />

        {/* 按当前 mode 渲染输入区主体。所有「问用户」的形态（ask/confirm/
            approval/form/ask_many）都由唯一的 QuestionMode 承接，不再分组件。 */}
        {activeDecision ? (
          <QuestionMode
            key={activeDecision.id}
            decision={activeDecision}
            onResolve={dequeueDecision}
            onAction={setDecisionAction}
          />
        ) : composerMode === "fn-form" && fnFormFunction ? (
          <FunctionForm
            // `key` ties to fn name so React re-mounts on every
            // switch — the freshly mounted header/body run their own
            // fadeIn animation, completing the crossfade with the
            // outgoing overlay below.
            key={fnFormFunction.name}
            fn={fnFormFunction}
            values={fnForm.values}
            setValue={fnForm.setValue}
            workdir={fnForm.workdir}
            setWorkdir={fnForm.setWorkdir}
            errorParam={fnForm.error}
            closing={fnForm.closing}
            onClose={handleFnFormClose}
            onSubmit={submitFnForm}
          />
        ) : (
          <ChatInputRow
            textareaRef={textareaRef}
            input={input}
            setInput={setInput}
            placeholder={isRunning
              ? text("type to steer the running task…", "输入以干预正在运行的任务…")
              : undefined}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            onFocus={() => slash.setFocused(true)}
            onBlur={() => slash.setFocused(false)}
            setCaretPos={setCaretPos}
            pastedEntries={pastedEntries}
            pasteMissing={pasteMissing}
            removePaste={removePaste}
            atToken={atToken}
            fileMatches={fileMatches}
            fileMenuIndex={fileMenuIndex}
            setFileMenuIndex={setFileMenuIndex}
            fileMenuLoading={fileMenuLoading}
            fileMenuPos={fileMenuPos}
            pickFile={pickFile}
          />
        )}
        {/* Outgoing fn-form overlay — only present during a fn → fn
            switch. Rendered AFTER the main form so that
            `querySelector('[data-fn-form-header]')` in the wrapper
            height measurement matches the main form first (the
            outgoing layer's cloned header/body would otherwise lock
            wrapper height to the previous form's size). Absolute +
            z-index 1 still puts it visually on top during the fade. */}
        {outgoingFn && (
          <div
            key={`${outgoingFn.name}-outgoing`}
            className={styles.outgoingLayer}
            aria-hidden="true"
          >
            <FunctionForm
              fn={outgoingFn}
              values={{}}
              setValue={noop}
              workdir=""
              setWorkdir={noop}
              errorParam={null}
              onClose={noop}
              onSubmit={noop}
              // Outgoing crossfade copy — strip input `id`s so the
              // browser doesn't complain about duplicate-id form
              // fields while both the live and ghost forms are
              // mounted simultaneously during the fade.
              ghost
            />
          </div>
        )}

        {/* Controls cluster placement: morphed modes keep the legacy
            internal bottom row — the wrapper height animation and the
            action-button glide measure against its 64px band. Chat
            mode renders the same cluster in the detached row below
            the wrapper instead (after .composerStack), matching the
            Claude Code three-band layout. */}
        {morphed && (
          <div key="bottom-row" className={`${styles.inputBottomRow} composer-bottom-row`}>
            {controlsCluster}
          </div>
        )}

        {/* Single send/stop button anchored at the wrapper level.
            `top` is mutated via inline style by the wrapper-height
            useLayoutEffect so the button glides between its chat-mode
            position (top: 16) and the fn-form position
            (top: wrapper.height − 48) over the same 0.3s curve as the
            wrapper itself — one continuous motion instead of a row-to
            -row teleport.
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
      {!morphed && (
        <div className={`${styles.controlsRow} composer-bottom-row`}>
          {controlsCluster}
        </div>
      )}
    </div>
  );
}


/** Status chip — the old topbar StatusBadge chip form (tone-tinted
 *  chip + indicator dot + channel label, ChannelMenu popover), re-hosted
 *  in the env-chip row above the input box (Claude's "Local" position).
 *  Reads the same store slice the tab-strip StatusDot reads; renders
 *  the legacy `.status-badge` classes so the tone modifiers
 *  (connecting / disconnected / paused) and `.indicator-dot` styling
 *  come from the global sheet.
 *
 *  This instance HOLDS `id="statusBadge"`: the legacy ui.ts updaters
 *  (lib/runtime-bridge/ui.ts) guard on that id before pushing status
 *  into the store, and `setStatusDotHealth` looks up `.indicator-dot`
 *  inside it. Exactly one element may carry the id — the tab strip's
 *  StatusDot copy is being removed. */
function StatusChip() {
  const { text } = useTranslation();
  const statusBadge = useSessionStore((s) => s.statusBadge);
  const [open, setOpen] = useState(false);

  // Another top-bar-family dropdown opening closes this one, so only
  // one is ever open (same coordination event as the other chips).
  useEffect(() => {
    const close = () => setOpen(false);
    window.addEventListener("topbar-close-menus", close);
    return () => window.removeEventListener("topbar-close-menus", close);
  }, []);

  function onOpenChange(next: boolean) {
    if (next) {
      window.dispatchEvent(new Event("topbar-close-menus"));
      (
        window as unknown as { _closeAllPopovers?: () => void }
      )._closeAllPopovers?.();
    }
    setOpen(next);
  }

  // Tone → the legacy `.status-badge` modifier (green default, yellow
  // connecting/paused, red disconnected) + the matching indicator-dot
  // colour class. `paused` wins over the raw tone, mirroring the type's
  // contract in lib/session-store/types.ts.
  const toneClass = statusBadge.paused
    ? " paused"
    : statusBadge.tone === "connecting"
      ? " connecting"
      : statusBadge.tone === "err"
        ? " disconnected"
        : statusBadge.tone === "warn"
          ? " paused"
          : "";
  const dotMod =
    statusBadge.tone === "ok"
      ? "--ok"
      : statusBadge.tone === "err"
        ? "--err"
        : "--warn";
  const label = statusBadge.label || text("Local", "本地");
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <HoverTip
        label={statusBadge.title || text("Conversation channel", "会话渠道")}
      >
        <PopoverTrigger asChild>
          <span
            id="statusBadge"
            role="button"
            className={`status-badge${toneClass}`}
          >
            <span className={`indicator-dot sm ${dotMod}`} aria-hidden="true" />
            <span className="badge-short">{label}</span>
          </span>
        </PopoverTrigger>
      </HoverTip>
      <PopoverContent
        side="top"
        align="start"
        sideOffset={6}
        className="w-auto border-0 bg-transparent p-0 shadow-none"
      >
        <ChannelMenu onClose={() => setOpen(false)} />
      </PopoverContent>
    </Popover>
  );
}

/** Drop-overlay positioned over the central chat column rather than
 *  the whole window. Anchored to ``#chatArea`` by bounding rect so
 *  the sidebars stay clear. Falls back to centred-of-viewport when
 *  the element isn't found (settings / functions / etc routes). */
function ScopedDropOverlay() {
  const [rect, setRect] = useState<DOMRect | null>(null);
  useLayoutEffect(() => {
    function measure() {
      const el = document.getElementById("chatArea")
        // ``main`` is the shared shell wrapper used by other pages
        // (functions / skills / mcp / memory). Fallback so drag-into-
        // settings still shows a sensible overlay.
        || document.querySelector(".main");
      if (el) setRect(el.getBoundingClientRect());
      else setRect(null);
    }
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const style: React.CSSProperties = rect ? {
    position: "fixed",
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
  } : {
    position: "fixed",
    inset: 0,
  };
  return (
    <div
      style={{
        ...style,
        zIndex: 10_000,
        background: "rgba(10,10,12,0.55)",
        backdropFilter: "blur(2px)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        pointerEvents: "none",
        borderRadius: 8,
        animation: "overlayIn 140ms ease-out",
      }}
    >
      <div
        style={{
          padding: "32px 48px",
          borderRadius: 14,
          border: "2px dashed rgba(255,255,255,0.4)",
          background: "rgba(20,20,24,0.85)",
          color: "var(--text-primary, #f5f5f5)",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 10,
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
        }}
      >
        <span style={{ fontSize: 36 }} aria-hidden>📎</span>
        <span style={{ fontSize: 16, fontWeight: 600 }}>
          Drop to attach
        </span>
        <span style={{ fontSize: 12, opacity: 0.7 }}>
          Images preview inline · text files inline as content ·
          others attach by name
        </span>
      </div>
    </div>
  );
}
