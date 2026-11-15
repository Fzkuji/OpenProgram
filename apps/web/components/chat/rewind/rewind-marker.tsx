"use client";

/** The transcript row left by a rewind, with Undo or Redo. Switching never
 *  changes files. */
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { useTranslation } from "@/lib/i18n";
import { formatMessageTime } from "@/lib/chat/transcript/message-time";
import { refreshRewindMarks, switchRewindMark } from "@/lib/chat/rewind/rewind-actions";
import {
  disabledReason,
  markAction,
  markLabel,
  markTip,
  needsTime,
  type RewindMark,
} from "@/lib/chat/rewind/rewind-logic";
import { useRewindStore } from "@/lib/chat/rewind/rewind-store";
import { useRewindDisabled } from "@/lib/chat/rewind/use-rewind-disabled";

const NO_MARKS: RewindMark[] = [];

/** This conversation's marks, read again as its transcript changes. */
export function useRewindMarks(sessionId: string | null, lastId: string | null, running: boolean): RewindMark[] {
  useEffect(() => {
    if (sessionId) void refreshRewindMarks(sessionId);
  }, [sessionId, lastId, running]);
  return useRewindStore(s => (sessionId ? s.marks[sessionId] ?? NO_MARKS : NO_MARKS));
}

export function RewindMarkers({ sessionId, marks }: { sessionId: string | null; marks: RewindMark[] }) {
  const { text } = useTranslation();
  const cause = useRewindDisabled(sessionId);
  if (!sessionId) return null;
  return (
    <>
      {marks.map(mark => {
        const gone = mark.available === false;
        const reason = cause ? disabledReason(cause, text) : null;
        const time = needsTime(mark, marks) && mark.at
          ? ` · ${formatMessageTime(new Date(mark.at > 1e12 ? mark.at : mark.at * 1000))}`
          : "";
        const action = markAction(mark, text);
        return (
          <div key={mark.edge_id} className="message system-event" data-rewind-mark={mark.variant}>
            <span className="system-event-rule" aria-hidden />
            <span className="system-event-text flex items-center gap-1.5">
              {gone ? (
                <HoverTip label={<TipBody title={markTip(mark, text)} />}>
                  <span tabIndex={0}>{markLabel(mark, text)}{time}</span>
                </HoverTip>
              ) : markLabel(mark, text)}
              {gone ? null : time}
              {gone ? null : (
                <HoverTip label={<TipBody title={reason ?? action} detail={reason ? undefined : markTip(mark, text)} />}>
                  <span className="inline-flex" tabIndex={reason ? 0 : -1}>
                    <Button
                      variant="ghost"
                      size="xs"
                      disabled={Boolean(reason)}
                      onClick={() => void switchRewindMark(sessionId, mark)}
                    >
                      {action}
                    </Button>
                  </span>
                </HoverTip>
              )}
            </span>
            <span className="system-event-rule" aria-hidden />
          </div>
        );
      })}
    </>
  );
}
