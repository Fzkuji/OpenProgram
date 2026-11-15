"use client";

/** What `/rewind` opens: every message of the conversation that can be gone
 *  back to, oldest first with the newest selected. */
import { useEffect, useRef, useState } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MENU_TRAILING, itemCls } from "@/components/ui/menu/menu-styles";
import { useTranslation } from "@/lib/i18n";
import { closeRewindPicker, pickRewindPoint } from "@/lib/chat/rewind/rewind-actions";
import { pickerLabel, type RewindPoint } from "@/lib/chat/rewind/rewind-logic";
import { useRewindStore } from "@/lib/chat/rewind/rewind-store";
import { parseAttachments } from "../messages/attachments/user-attachments";

function labelOf(point: RewindPoint): string {
  const parsed = parseAttachments(point.user_text || point.summary || "");
  return pickerLabel(parsed.text, parsed.attachments.map(item => item.filename));
}

export function RewindPicker() {
  const { text } = useTranslation();
  const picker = useRewindStore(s => s.picker);
  const points = picker?.points ?? null;
  const [selected, setSelected] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (points) setSelected(Math.max(0, points.length - 1));
  }, [points]);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${selected}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [selected, points]);
  if (!picker) return null;
  const pick = (point: RewindPoint | undefined) => {
    if (point) pickRewindPoint(picker.sessionId, point);
  };
  return (
    <Dialog open onOpenChange={next => { if (!next) closeRewindPicker(); }}>
      <DialogContent
        showCloseButton={false}
        aria-label={text("Rewind session", "回退会话")}
        className="sm:max-w-lg"
        onOpenAutoFocus={event => {
          event.preventDefault();
          listRef.current?.focus();
        }}
      >
        <DialogHeader>
          <DialogTitle>{text("Rewind", "回退")}</DialogTitle>
          <DialogDescription>
            {text("Go back to an earlier message in this conversation.", "回到这段对话里更早的一条消息。")}
          </DialogDescription>
        </DialogHeader>
        <div
          ref={listRef}
          role="listbox"
          tabIndex={0}
          aria-label={text("Messages", "消息")}
          aria-activedescendant={points?.length ? `rewind-point-${selected}` : undefined}
          className="flex max-h-[50vh] flex-col overflow-y-auto outline-none"
          onKeyDown={event => {
            if (!points?.length) return;
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              event.preventDefault();
              const step = event.key === "ArrowDown" ? 1 : -1;
              setSelected(index => Math.min(points.length - 1, Math.max(0, index + step)));
            } else if (event.key === "Enter") {
              event.preventDefault();
              pick(points[selected]);
            }
          }}
        >
          {points === null ? (
            <p className="px-1.5 py-1 text-fs-sm text-text-muted">{text("Loading…", "加载中…")}</p>
          ) : points.length === 0 ? (
            <p className="px-1.5 py-1 text-fs-sm italic text-text-muted">{text("Nothing to rewind to yet", "还没有可回退的消息")}</p>
          ) : points.map((point, index) => (
            <div
              key={point.msg_id}
              id={`rewind-point-${index}`}
              data-index={index}
              role="option"
              aria-selected={index === selected}
              className={itemCls(index === selected)}
              onMouseEnter={() => setSelected(index)}
              onClick={() => pick(point)}
            >
              <span className="min-w-0 flex-1 truncate">{labelOf(point)}</span>
              {point.code_available === false ? (
                <span className={`${MENU_TRAILING} text-fs-xs text-text-muted`}>
                  {text("Conversation only", "仅对话")}
                </span>
              ) : null}
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
