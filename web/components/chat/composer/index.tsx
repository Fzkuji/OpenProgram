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

import { useSessionStore } from "@/lib/session-store";

import { ContextBadge } from "../context-badge";
import { FunctionForm, visibleParams } from "./fn-form/fn-form";
import {
  PlusIcon,
  SendIcon,
  StopIcon,
  ToolsIcon,
  WebSearchIcon,
} from "./icons";
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
import { useFnFormState } from "./fn-form/use-fn-form-state";
import { useFnFormWrapper } from "./fn-form/use-fn-form-wrapper";
import { useSlashMenu } from "./slash/use-slash-menu";
import { useThinkingEffort } from "./controls/use-thinking-effort";
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

export function Composer() {
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
    onDragOver,
    onDragLeave,
    onDrop,
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
    [input, setInput, addImages],
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
  const send = wsSend;

  const isRunning = runningTask !== null;
  const fnFormActive = fnFormFunction !== null;

  // @file mention — state + debounced /api/file-search + popover
  // positioning + picker all live in ./use-file-mention now. The hook
  // owns the 6 useStates + 2 effects + pickFile callback that used to
  // sit here.
  const {
    atToken,
    caretPos,
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
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  const {
    tools: toolsEnabled,
    webSearch: webSearchEnabled,
    toggleTools,
    toggleWebSearch,
  } = useToolsToggles();
  // Slash-menu state lives in its own hook (./use-slash-menu).
  // fn-form field state (values, workdir, error highlight, closing
  // flag) is owned by `./use-fn-form-state`; it also runs the
  // default-value seeding effect on fn change.
  const fnForm = useFnFormState(fnFormFunction);

  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const sendBtnRef = useRef<HTMLButtonElement>(null);
  // Refs:
  //  - `plusTriggerRef` / `plusMenuRef`: the plus menu is still portal'd
  //    into `document.body` to escape `.inputWrapper { overflow: hidden }`.
  //    We measure the trigger to place the popover and use the menu ref
  //    so the click-outside handler treats clicks inside the menu as
  //    "still inside the composer".
  //  - `thinkingTriggerRef`: the effort pill expands inline (no portal).
  //    Since it lives inside `.inputWrapper`, the wrapper-contains check
  //    already covers clicks on it — no separate menu ref needed.
  const thinkingTriggerRef = useRef<HTMLDivElement>(null);
  const plusTriggerRef = useRef<HTMLButtonElement>(null);
  const plusMenuRef = useRef<HTMLDivElement>(null);
  const [plusMenuPos, setPlusMenuPos] = useState<
    { left: number; bottom: number } | null
  >(null);

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
      fnForm.setClosing(false);
    }, [closeFnFormStore, fnForm.setClosing]),
    wrapperRef,
    sendBtnRef,
  });

  // Auto-resize the textarea as content changes.
  useEffect(() => {
    const t = textareaRef.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${Math.min(t.scrollHeight, 200)}px`;
  }, [input]);

  // External focus requests via the store (welcome buttons,
  // retry helpers, etc.).
  useEffect(() => {
    if (focusTick === 0) return;
    textareaRef.current?.focus();
  }, [focusTick]);

  // Close popovers on outside click — but the two popovers have
  // different "what counts as outside" rules:
  //
  // - Effort pill is INLINE inside the composer wrapper. A click in
  //   the textarea or on any other composer control should collapse
  //   it (otherwise expanded state lingers as the user keeps typing).
  //   So we check `thinkingTriggerRef.contains` directly — anything
  //   outside the pill itself counts as outside.
  //
  // - Plus menu is PORTAL'D into `document.body` to escape
  //   `.inputWrapper { overflow: hidden }`. Its trigger is in the
  //   wrapper but its menu lives at the document root, so the "stays
  //   open" set is `wrapper ∪ plusMenuRef`. Anywhere else closes it.
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
      }
      if (!wrapper.contains(t) && !plusMenuRef.current?.contains(t)) {
        setPlusMenuOpen(false);
      }
    }
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, []);

  useLayoutEffect(() => {
    if (!plusMenuOpen) {
      setPlusMenuPos(null);
      return;
    }
    const trigger = plusTriggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    setPlusMenuPos({
      left: rect.left,
      bottom: window.innerHeight - rect.top + 4,
    });
  }, [plusMenuOpen]);

  // Slash menu (state + open/close timing + command dispatch).
  const slash = useSlashMenu({ input, textareaRef, send });

  /* ---- Submit -------------------------------------------------------- */

  const submit = useCallback(async () => {
    if (isRunning) return;
    const trimmed = input.trim();
    // Allow image-only submits — the LLM can answer "describe this
    // screenshot" without text. Otherwise require at least one of
    // text or attached image.
    if (!trimmed && pendingImages.length === 0 && pendingDocs.length === 0) {
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
    // Inline attached docs: text-y ones become <file> blocks at the
    // top of the message; binary ones just announce themselves as a
    // metadata line so the LLM knows the user dropped something even
    // if it can't read it.
    if (pendingDocs.length > 0) {
      const blocks: string[] = [];
      const mentions: string[] = [];
      for (const d of pendingDocs) {
        if (d.content !== null) {
          blocks.push(
            `<file name="${d.filename}">\n${d.content}\n</file>`,
          );
        } else {
          mentions.push(`[attached: ${d.filename} (${d.ext || "binary"}, `
            + `${Math.max(1, Math.round(d.sizeBytes / 1024))} KB)]`);
        }
      }
      const prefix = [...blocks, ...mentions].join("\n");
      expanded = prefix ? `${prefix}\n\n${expanded}` : expanded;
    }
    const imagesPayload = pendingImages.map((p) => p.attachment);
    // Delegate to legacy `sendMessage` (chat.js) so the user bubble +
    // welcome-hide + assistant placeholder + isRunning flip all fire
    // before the WS payload goes out. Composer is just the trigger.
    const handled = sendChatMessage({
      text: expanded,
      attachments: imagesPayload.length > 0 ? imagesPayload : undefined,
      thinking,
      toolsEnabled,
      webSearchEnabled,
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
    pendingDocs,
    pendingImages,
    send,
    setInput,
    slash,
    thinking,
    toolsEnabled,
    webSearchEnabled,
  ]);

  function stop() {
    if (!currentSessionId) return;
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
    fnForm.setClosing(true);
    // Mirror into the store so the welcome screen flips its examples
    // row out of the collapsed state NOW — in sync with the form
    // shrinking — instead of a beat later when `fnFormFunction`
    // finally clears at transition end.
    setFnFormClosing(true);
  }, [fnForm, setFnFormClosing]);

  const submitFnForm = useCallback(() => {
    if (!fnFormFunction || isRunning) return;
    const fn = fnFormFunction;
    const workdirMode = fn.workdir_mode ?? "optional";
    const wd = fnForm.workdir.trim();
    if (workdirMode === "required" && !wd) {
      fnForm.setError("__workdir");
      return;
    }

    const parts: string[] = ["run", fn.name];
    for (const p of visibleParams(fn)) {
      const isBool = p.type === "bool" || p.type === "boolean";
      let v = (fnForm.values[p.name] ?? "").trim();
      if (!v && isBool) v = "False";
      if (!v && !p.required) continue;
      if (!v && p.required) {
        fnForm.setError(p.name);
        return;
      }
      if (v.indexOf(" ") !== -1 || v.indexOf('"') !== -1) {
        parts.push(`${p.name}=${JSON.stringify(v)}`);
      } else {
        parts.push(`${p.name}=${v}`);
      }
    }
    if (workdirMode !== "hidden") {
      if (wd.indexOf(" ") !== -1 || wd.indexOf('"') !== -1) {
        parts.push(`work_dir=${JSON.stringify(wd)}`);
      } else {
        parts.push(`work_dir=${wd}`);
      }
    }

    const command = parts.join(" ");
    // Same legacy hand-off as plain chat — sendMessage detects the
    // `run ...` prefix and renders the runtime block instead of a
    // user message bubble, then writes the WS payload.
    const handled = sendChatMessage({
      text: command,
      thinking,
      toolsEnabled,
      webSearchEnabled,
    });
    if (!handled) {
      const ok = send({
        action: "chat",
        text: command,
        session_id: currentSessionId ?? null,
        thinking_effort: thinking,
        tools: toolsEnabled,
        web_search: webSearchEnabled,
      });
      if (!ok) return;
    }
    handleFnFormClose();
  }, [
    currentSessionId,
    fnFormFunction,
    fnForm,
    handleFnFormClose,
    isRunning,
    send,
    thinking,
    toolsEnabled,
    webSearchEnabled,
  ]);

  const onSendButtonClick = fnFormActive ? submitFnForm : submit;
  // In chat mode: disabled when textarea is empty OR when a paste
  //   token references content that was lost (chip is red). Submitting
  //   in the "lost" state would silently strip the token — see the
  //   submit() guard mirror.
  // In fn-form mode: disabled when any required param has no value,
  //   OR when workdir is required and empty.
  const sendDisabled = fnFormActive
    ? (() => {
        const fn = fnFormFunction!;
        const workdirMode = fn.workdir_mode ?? "optional";
        if (workdirMode === "required" && !fnForm.workdir.trim()) return true;
        for (const p of visibleParams(fn)) {
          if (!p.required) continue;
          const v = (fnForm.values[p.name] ?? "").trim();
          if (!v) return true;
        }
        return false;
      })()
    : !input.trim() || pasteMissing.size > 0;
  const sendTitle = fnFormActive
    ? "Run"
    : pasteMissing.size > 0
    ? "Paste content lost — remove the red chip and re-paste"
    : "Send message";

  /* ---- Render -------------------------------------------------------- */

  const anyToolActive = toolsEnabled || webSearchEnabled;

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
        className={`${styles.inputWrapper} ${fnFormActive ? styles.fnFormMode : ""}`}
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

        {fnFormFunction ? (
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

        <div key="bottom-row" className={styles.inputBottomRow}>
          <div className={styles.inputOptions}>
            <button
              ref={plusTriggerRef}
              className={`${styles.plusBtn} ${anyToolActive ? styles.hasActive : ""}`}
              onClick={(e) => {
                e.stopPropagation();
                setPlusMenuOpen((v) => !v);
                setThinkingMenuOpen(false);
              }}
              title="Add tools, files, and more"
              aria-label="More options"
              type="button"
            >
              <PlusIcon />
            </button>

            <div className={styles.activeToolChips}>
              {toolsEnabled && (
                <ToolChip
                  icon={<ToolsIcon size={16} />}
                  label="Tools"
                  onRemove={toggleTools}
                />
              )}
              {webSearchEnabled && (
                <ToolChip
                  icon={<WebSearchIcon size={16} />}
                  label="Web Search"
                  onRemove={toggleWebSearch}
                />
              )}
            </div>

            {plusMenuOpen && plusMenuPos && typeof document !== "undefined"
              ? createPortal(
                  <div
                    ref={plusMenuRef}
                    className={styles.plusMenu}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      position: "fixed",
                      left: plusMenuPos.left,
                      bottom: plusMenuPos.bottom,
                      top: "auto",
                      marginBottom: 0,
                    }}
                  >
                    <PlusMenuItem
                      active={toolsEnabled}
                      onClick={toggleTools}
                      icon={<ToolsIcon />}
                      label="Tools"
                      title="Shell, read/write/edit, grep/glob, list, patch, todo"
                    />
                    <PlusMenuItem
                      active={webSearchEnabled}
                      onClick={toggleWebSearch}
                      icon={<WebSearchIcon />}
                      label="Web Search"
                      title="Give the agent web search this turn"
                    />
                    <PlusMenuItem
                      active={pendingImages.length > 0}
                      onClick={() => {
                        setPlusMenuOpen(false);
                        onPickImages();
                      }}
                      icon={<span aria-hidden style={{ fontSize: 14 }}>🖼</span>}
                      label="Attach image"
                      title="Attach one or more images (or just paste / drag-drop)"
                    />
                  </div>,
                  document.body,
                )
              : null}

            <ThinkingEffortPill
              ref={thinkingTriggerRef}
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
          <div className={styles.inputBottomRight}>
            <ContextBadge />
          </div>
        </div>

        {/* Single send/stop button anchored at the wrapper level.
            `top` is mutated via inline style by the wrapper-height
            useLayoutEffect so the button glides between its chat-mode
            position (top: 16) and the fn-form position
            (top: wrapper.height − 48) over the same 0.3s curve as the
            wrapper itself — one continuous motion instead of a row-to
            -row teleport. */}
        <button
          ref={sendBtnRef}
          className={`${styles.actionBtn} ${isRunning ? styles.stopBtn : styles.sendBtn}`}
          onClick={isRunning ? stop : onSendButtonClick}
          disabled={!isRunning && sendDisabled}
          title={isRunning ? "Stop" : sendTitle}
          type="button"
        >
          {isRunning ? <StopIcon /> : <SendIcon />}
        </button>

        {/* Close button — wrapper-level so it stays mounted across
            fn-form switches (no blink on the icon when the header
            unmounts/remounts with a new key). Only visible while
            fn-form is open and not in the middle of closing. */}
        {fnFormActive && !fnForm.closing && (
          <button
            className={styles.closeBtn}
            type="button"
            onClick={handleFnFormClose}
            onMouseDown={(e) => e.preventDefault()}
            tabIndex={-1}
            title="Close"
            aria-label="Close"
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
    </div>
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
