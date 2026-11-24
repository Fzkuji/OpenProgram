"use client";

/** Annotate mode for a desktop web tab: the toolbar button's state, and
 *  the send that turns the page's notes into one chat message. The overlay
 *  itself runs in the page (apps/desktop/main/web-views/annotate-overlay.js). */
import { useEffect, useRef, useState } from "react";
import type { DesktopBridge } from "@/lib/desktop/desktop-bridge";
import { useTranslation } from "@/lib/i18n";
import { showToast } from "@/lib/format-utils/toast";
import {
  annotationFileName,
  annotationMessage,
  type AnnotateEvent,
  type AnnotateLabels,
  type AnnotateTheme,
} from "@/lib/browser/capture/annotate";
import { ownerChat } from "@/lib/chat/resources/target-chat";
import { sendFromOutside } from "@/lib/chat/send/send-queue";
import { readImageFile, type PendingImage } from "@/components/chat/composer/attach/image/image-attach";

type Text = (en: string, zh: string) => string;

function annotateLabels(text: Text): AnnotateLabels {
  return {
    hint: text("Click an element or drag a box · Esc to finish", "点击元素或拖出方框 · Esc 结束"),
    placeholder: text("What should change?", "要改成什么样？"),
    delete: text("Delete", "删除"),
    adjust: text("Adjust", "调整"),
    size: text("Size", "大小"),
    spacing: text("Spacing", "间距"),
    color: text("Color", "颜色"),
    countOne: text("1 note on this page", "本页 1 条批注"),
    countMany: text("{n} notes on this page", "本页 {n} 条批注"),
    discard: text("Discard", "丢弃"),
    send: text("Send to chat", "发送到对话"),
  };
}

/** The App's live colours, so the cards inside the page match the theme. */
function appTheme(): AnnotateTheme {
  const style = getComputedStyle(document.body);
  const read = (name: string) => style.getPropertyValue(name).trim();
  return {
    bg: read("--bg-primary"),
    border: read("--border-light"),
    text: read("--text-primary"),
    muted: read("--text-muted"),
    accent: read("--accent-blue"),
  };
}

async function screenshotImages(event: Extract<AnnotateEvent, { type: "send" }>): Promise<PendingImage[]> {
  if (!event.screenshot) return [];
  try {
    const name = annotationFileName(event.url);
    const blob = await (await fetch(event.screenshot)).blob();
    return [await readImageFile(new File([blob], name, { type: "image/png" }), name)];
  } catch {
    // The notes still go; the message then does not mention a screenshot.
    return [];
  }
}

async function sendNotes(
  bridge: DesktopBridge,
  tabId: string,
  event: Extract<AnnotateEvent, { type: "send" }>,
  text: Text,
): Promise<void> {
  const chat = ownerChat(tabId);
  if (!chat || event.notes.length === 0) {
    if (!chat) showToast(text("Open a conversation to send the notes to", "先打开一个对话，再发送批注"));
    void bridge.webTab.annotate?.(tabId, "unsent");
    return;
  }
  const images = await screenshotImages(event);
  const message = annotationMessage({ url: event.url, title: event.title }, event.notes, images.length > 0);
  if (!sendFromOutside(chat.key, message, { images })) {
    void bridge.webTab.annotate?.(tabId, "unsent");
    return;
  }
  void bridge.webTab.annotate?.(tabId, "sent");
  showToast(chat.title
    ? text(`Notes sent to “${chat.title}”`, `批注已发送到“${chat.title}”`)
    : text("Notes sent to the conversation", "批注已发送到对话"));
}

export function usePageAnnotate(bridge: DesktopBridge, tabId: string, url: string) {
  const { text } = useTranslation();
  const textRef = useRef(text);
  textRef.current = text;
  const [active, setActive] = useState(false);
  const supported = typeof bridge.webTab.annotate === "function"
    && typeof bridge.webTab.onAnnotateEvent === "function";

  useEffect(() => bridge.webTab.onAnnotateEvent?.((event) => {
    if (event.id !== tabId) return;
    if (event.type === "state") setActive(event.active);
    else if (event.type === "send") void sendNotes(bridge, tabId, event, textRef.current);
  }), [bridge, tabId]);

  // Notes belong to the page they were written on: a new address clears
  // them (a remount at the same address keeps them).
  const lastUrl = useRef(url);
  useEffect(() => {
    if (lastUrl.current === url) return;
    lastUrl.current = url;
    setActive(false);
    void bridge.webTab.annotate?.(tabId, "discard");
  }, [bridge, tabId, url]);

  // A pane that goes away (another tab chosen) leaves the mode; the notes
  // stay on the page until Discard or Send.
  useEffect(() => () => { void bridge.webTab.annotate?.(tabId, "exit"); }, [bridge, tabId]);

  function start() {
    void bridge.webTab.annotate?.(tabId, "start", { labels: annotateLabels(textRef.current), theme: appTheme() })
      .then((ok) => { if (ok) setActive(true); });
  }

  function exit() {
    setActive(false);
    void bridge.webTab.annotate?.(tabId, "exit");
  }

  return { supported, active, start, exit };
}
