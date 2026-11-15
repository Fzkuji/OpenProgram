"use client";

/** Ways out of a failed turn: Rewind opens the picker, and Rewind and retry
 *  takes a rejected prompt back to the composer. */
import { Button } from "@/components/ui/button";
import { HoverTip, TipBody } from "@/components/ui/tooltip";
import { selectRow, selectViewRows, useSessionStore, type ChatMsg } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { turnUserMessageId } from "@/lib/chat/transcript/chapter";
import { openRewindPicker, rewindAndRetry } from "@/lib/chat/rewind/rewind-actions";
import { disabledReason, isLastPrompt, retryHelps, rewindHelps } from "@/lib/chat/rewind/rewind-logic";
import { useRewindAllowed, useRewindDisabled } from "@/lib/chat/rewind/use-rewind-disabled";

/** Whether this failed reply's card offers Rewind right now. */
export function useRewindOffered(sessionId: string | null | undefined, reason: string | null | undefined): boolean {
  const cause = useRewindDisabled(sessionId);
  const allowed = useRewindAllowed(sessionId);
  return Boolean(sessionId) && allowed && !cause && rewindHelps(reason);
}

export function RewindErrorActions({ msg, sessionId }: { msg: ChatMsg; sessionId: string | null | undefined }) {
  const { text } = useTranslation();
  const cause = useRewindDisabled(sessionId);
  const allowed = useRewindAllowed(sessionId);
  const prompt = useSessionStore(s => {
    if (!sessionId) return null;
    const order = s.messageOrder?.[sessionId];
    const rows = selectViewRows(sessionId)(s);
    const id = turnUserMessageId(order, rows, msg.id);
    return id && isLastPrompt(order, rows, id) ? id : null;
  });
  if (!sessionId || !allowed || !rewindHelps(msg.errorReason)) return null;
  const reason = cause ? disabledReason(cause, text) : undefined;
  return (
    <div className="mt-1 flex gap-1.5">
      <HoverTip label={<TipBody
        title={reason ?? text("Rewind", "回退")}
        detail={reason ? undefined : text("Pick an earlier message to go back to.", "选择一条更早的消息回到那里。")}
      />}>
        <span className="inline-flex" tabIndex={reason ? 0 : -1}>
          <Button variant="elevated" size="xs" disabled={Boolean(reason)} onClick={() => void openRewindPicker(sessionId, { fromError: true })}>
            {text("Rewind", "回退")}
          </Button>
        </span>
      </HoverTip>
      {prompt && retryHelps(msg.errorReason) ? (
        <HoverTip label={<TipBody
          title={reason ?? text("Rewind and retry", "回退并重试")}
          detail={reason ? undefined : text("Takes this message out of the conversation and back to the input box. Files stay as they are.", "把这条消息撤出对话并放回输入框，文件保持现状。")}
        />}>
          <span className="inline-flex" tabIndex={reason ? 0 : -1}>
            <Button
              variant="elevated"
              size="xs"
              disabled={Boolean(reason)}
              onClick={() => {
                const content = selectRow(sessionId, prompt)(useSessionStore.getState())?.content ?? "";
                void rewindAndRetry(sessionId, prompt, content);
              }}
            >
              {text("Rewind and retry", "回退并重试")}
            </Button>
          </span>
        </HoverTip>
      ) : null}
    </div>
  );
}
