"use client";

// Cmd/Ctrl+F find bar for the conversation. The server searches the whole
// branch; a match outside the loaded history window is loaded around before
// the chat scrolls to it. The pill is the web tab's find pill, floated over
// the top-right of the chat column.

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";
import { wsRequest } from "@/lib/net/ws/ws-request";
import { useSessionStore } from "@/lib/session-store";
import { captureHistoryAnchor } from "@/lib/chat/history/history-viewport";
import { loadSessionHistoryWindow } from "@/lib/runtime-bridge/session/session-history-loader";
import {
  chatOwnsFind, clearHighlights, locate, messageRow, normalizeMatches, paintHighlights, revealForFind, step,
  textRanges, totalOccurrences, type FindMatch,
} from "@/lib/chat/transcript/conversation-find";
import pill from "../../../center-tabs/browser/browser.module.css";
import styles from "./conversation-find.module.css";

type SearchReply = { id?: string; query?: string; matches?: unknown; error_code?: string };

const QUERY_DELAY_MS = 150;

function findButton() {
  return buttonVariants({ variant: "ghost", size: "icon-xs" });
}

async function searchSession(sessionId: string, query: string): Promise<FindMatch[] | null> {
  const reply = await wsRequest<SearchReply>("search_session", {
    session_id: sessionId, query,
  }, "session_search_results", { requestId: true }, 15000);
  if (!reply || reply.error_code || reply.id !== sessionId) return null;
  return normalizeMatches(reply.matches);
}

/** The match nearest the reading position: the first one at or below it
 *  in the loaded window, else the latest one. */
function startIndex(sessionId: string, matches: readonly FindMatch[]): number {
  if (!matches.length) return -1;
  const order = useSessionStore.getState().messageOrder[sessionId] ?? [];
  const area = document.getElementById("chatArea");
  const anchor = area ? captureHistoryAnchor(area)?.id : undefined;
  const from = anchor ? Math.max(0, order.indexOf(anchor)) : 0;
  let index = 0;
  for (const match of matches) {
    const at = order.indexOf(match.message_id);
    if (at >= from) return index;
    index += match.count;
  }
  return totalOccurrences(matches) - matches[matches.length - 1].count;
}

function nextFrame(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()));
}

export function ConversationFind({ sessionId, onTarget }: {
  sessionId: string | null;
  /** Keeps the current match's row rendered while the list recycles rows. */
  onTarget: (messageId: string | null) => void;
}) {
  const { text } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState<FindMatch[]>([]);
  const [index, setIndex] = useState(-1);
  const inputRef = useRef<HTMLInputElement>(null);
  const searchSeq = useRef(0);
  const current = useRef<{ messageId: string; occurrence: number } | null>(null);
  // Painting reads the query the shown matches came from, not every keystroke.
  const shownQuery = useRef("");
  const total = totalOccurrences(matches);

  const repaint = useCallback(() => {
    const container = document.getElementById("chatMessages");
    const needle = shownQuery.current;
    if (!container || !open || !needle) {
      clearHighlights();
      return;
    }
    const all = textRanges(container, needle);
    let mine: Range | null = null;
    const target = current.current;
    const row = target ? messageRow(container, target.messageId) : null;
    if (row && target) {
      const inRow = textRanges(row, needle);
      mine = inRow[Math.min(target.occurrence, inRow.length - 1)] ?? null;
    }
    paintHighlights(all, mine);
  }, [open]);

  const reveal = useCallback(async (list: readonly FindMatch[], at: number) => {
    const found = sessionId ? locate(list, at) : null;
    if (!found || !sessionId) {
      current.current = null;
      onTarget(null);
      repaint();
      return;
    }
    const { match, occurrence } = found;
    current.current = { messageId: match.message_id, occurrence };
    onTarget(match.message_id);
    const loaded = useSessionStore.getState().messageOrder[sessionId]?.includes(match.message_id);
    if (!loaded) await loadSessionHistoryWindow(sessionId, "around", match.id);
    if (current.current?.messageId !== match.message_id) return;
    const area = document.getElementById("chatArea");
    const container = document.getElementById("chatMessages");
    if (!area || !container) return;
    // A recycled row is a sized slot until it is live; bring it near first.
    const esc = CSS.escape(match.message_id);
    const slot = container.querySelector<HTMLElement>(`[data-msg-slot="${esc}"]`);
    if (!messageRow(container, match.message_id)) slot?.scrollIntoView({ block: "center" });
    for (let i = 0; i < 3 && !messageRow(container, match.message_id); i += 1) await nextFrame();
    if (current.current?.messageId !== match.message_id) return;
    const row = messageRow(container, match.message_id);
    if (!row) return;
    // A row that keeps its text folded (an agent's card, a message between
    // agents) opens, so the match is on screen before it is measured.
    if (revealForFind(match.message_id)) {
      await nextFrame();
      if (current.current?.messageId !== match.message_id) return;
    }
    const ranges = textRanges(row, shownQuery.current);
    const range = ranges[Math.min(occurrence, ranges.length - 1)];
    const box = (range ?? row).getBoundingClientRect();
    const view = area.getBoundingClientRect();
    if (box.top < view.top + 48 || box.bottom > view.bottom - 96) {
      area.scrollTop += box.top - view.top - area.clientHeight / 3;
      area.dispatchEvent(new Event("scroll"));
    }
    repaint();
  }, [sessionId, onTarget, repaint]);

  const close = useCallback(() => {
    searchSeq.current += 1;
    setOpen(false);
    setMatches([]);
    setIndex(-1);
    current.current = null;
    shownQuery.current = "";
    onTarget(null);
    clearHighlights();
  }, [onTarget]);

  // Cmd/Ctrl+F opens (or refocuses) the bar; Cmd/Ctrl+G steps while open.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || !(event.metaKey || event.ctrlKey) || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "f" && !event.shiftKey) {
        if (!sessionId || !chatOwnsFind(document.activeElement, document.getElementById("chatArea"))) return;
        event.preventDefault();
        setOpen(true);
        requestAnimationFrame(() => {
          inputRef.current?.focus();
          inputRef.current?.select();
        });
      } else if (key === "g" && open && total > 0) {
        event.preventDefault();
        setIndex((i) => step(i, total, !event.shiftKey));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sessionId, open, total]);

  // A new conversation starts with the bar closed.
  useEffect(() => close, [sessionId, close]);

  // Search as the query settles.
  useEffect(() => {
    if (!open || !sessionId) return;
    const needle = query.trim();
    const seq = ++searchSeq.current;
    if (!needle) {
      shownQuery.current = "";
      setMatches([]);
      setIndex(-1);
      return;
    }
    const timer = window.setTimeout(() => {
      void searchSession(sessionId, needle).then((found) => {
        if (seq !== searchSeq.current) return;
        const list = found ?? [];
        shownQuery.current = needle;
        setMatches(list);
        setIndex(startIndex(sessionId, list));
      });
    }, QUERY_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [open, sessionId, query]);

  useEffect(() => {
    if (open) void reveal(matches, index);
  }, [open, matches, index, reveal]);

  // Rows mount, recycle and stream: keep the highlights on what is shown.
  useEffect(() => {
    if (!open) return;
    const container = document.getElementById("chatMessages");
    if (!container) return;
    let frame = 0;
    const observer = new MutationObserver(() => {
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; repaint(); });
    });
    observer.observe(container, { childList: true, subtree: true, characterData: true });
    repaint();
    return () => {
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, [open, repaint]);

  const host = typeof document !== "undefined" ? document.getElementById("chatView") : null;
  if (!open || !host) return null;
  const label = text("Find in conversation", "在对话中查找");
  return createPortal(
    <div className={styles.anchor} role="search">
      <div className={pill.webFindPill}>
        <Search size={12} aria-hidden="true" />
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            // Enter and Escape belong to the input method while composing.
            if (event.nativeEvent.isComposing) return;
            if (event.key === "Escape") {
              event.preventDefault();
              close();
            } else if (event.key === "Enter" && total > 0) {
              event.preventDefault();
              setIndex((i) => step(i, total, !event.shiftKey));
            }
          }}
          placeholder={label}
          aria-label={label}
        />
        <span className={pill.webFindCount} aria-live="polite">
          {total > 0 ? `${index + 1} / ${total}` : "0 / 0"}
        </span>
        <span className={pill.webFindSep} aria-hidden="true" />
        <button type="button" className={findButton()} onClick={() => setIndex((i) => step(i, total, false))} disabled={!total} aria-label={text("Previous match", "上一个匹配项")}>
          <ChevronUp size={14} />
        </button>
        <button type="button" className={findButton()} onClick={() => setIndex((i) => step(i, total, true))} disabled={!total} aria-label={text("Next match", "下一个匹配项")}>
          <ChevronDown size={14} />
        </button>
        <button type="button" className={findButton()} onClick={close} aria-label={text("Close find", "关闭查找")}>
          <X size={14} />
        </button>
      </div>
    </div>,
    host,
  );
}
