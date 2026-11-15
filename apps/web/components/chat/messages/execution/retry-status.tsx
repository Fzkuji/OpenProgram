"use client";

/**
 * An automatic retry inside a reply (chat-turn-visual-spec.html §1): the
 * running tool group's summary row carries the state as a tag, and the
 * group's timeline ends with a row for it whose body is the failure. The
 * state goes away when the retry succeeds; nothing is kept.
 */
import { useEffect, useState } from "react";
import type { RetryStatus } from "@/lib/session-store";
import { useTranslation } from "@/lib/i18n";
import { ExecutionStrip, StepRow } from "./execution-strip";

type Text = ReturnType<typeof useTranslation>["text"];

/** Whole seconds left before the next attempt, ticking once a second. */
function useSecondsLeft(retry: RetryStatus): number {
  const due = (retry.receivedAt ?? 0) + (retry.delayMs ?? 0);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setNow(Date.now());
    if (due <= Date.now()) return;
    const timer = setInterval(() => {
      setNow(Date.now());
      if (Date.now() >= due) clearInterval(timer);
    }, 1000);
    return () => clearInterval(timer);
  }, [due]);
  return Math.max(0, Math.ceil((due - now) / 1000));
}

/** True when the retry answers a failure rather than continuing on purpose. */
export function retryIsFailure(retry: RetryStatus): boolean {
  return retry.reason !== "output_limit";
}

/** What a transport failure was, read from its one-line error. */
function transportCause(error: string | undefined, text: Text): string {
  const e = error || "";
  if (/\b429\b|rate.?limit|too many requests/i.test(e)) return text("Rate limited", "被限流");
  if (/\b(?:500|502|503|504|529)\b|overload|unavailable|server.?error/i.test(e)) return text("Server busy", "服务器繁忙");
  if (/time(?:d)?.?out/i.test(e)) return text("Timed out", "请求超时");
  return text("Connection lost", "连接中断");
}

export function retryLabel(retry: RetryStatus, seconds: number, text: Text): string {
  const count = retry.maxAttempts ? `${retry.attempt}/${retry.maxAttempts}` : String(retry.attempt);
  switch (retry.reason) {
    case "network_wait":
      return seconds > 0
        ? text(`Offline, checking again in ${seconds}s`, `网络断了，${seconds} 秒后再试`)
        : text("Offline, checking again", "网络断了，正在重试");
    case "transport": {
      const cause = transportCause(retry.error, text);
      return seconds > 0
        ? text(`${cause}, retrying in ${seconds}s · ${count}`, `${cause}，${seconds} 秒后重试 ${count}`)
        : text(`${cause}, retrying ${count}`, `${cause}，正在重试 ${count}`);
    }
    case "output_limit":
      return text("Hit the output limit, continuing", "输出到上限，继续生成");
    default:
      return text(`Reply came back incomplete, retrying ${count}`, `回复不完整，正在重试 ${count}`);
  }
}

export function useRetryLabel(retry: RetryStatus): string {
  const { text } = useTranslation();
  return retryLabel(retry, useSecondsLeft(retry), text);
}

/** The tag after a summary row's sentence; without `lead` it is the row. */
export function RetryTag({ retry, lead = true }: { retry: RetryStatus; lead?: boolean }) {
  const label = useRetryLabel(retry);
  const tone = retryIsFailure(retry) ? " is-failure" : "";
  return (
    <>
      {lead ? <span className={"tl-retry" + tone} aria-hidden="true">·</span> : null}
      <span className={"tl-retry" + tone} role="status" aria-live="polite">{label}</span>
    </>
  );
}

/** The timeline's last row while the retry waits; its body is the failure. */
export function RetryStep({ retry }: { retry: RetryStatus }) {
  const label = useRetryLabel(retry);
  return (
    <StepRow
      icon="function"
      glyph={retry.reason === "output_limit" ? "restart" : "plug-circle"}
      tone="system"
      title={label}
      note={retry.error}
      running
      copyText={retry.error}
      inlineBody={retry.error ? <div className="tl-retry-error">{retry.error}</div> : undefined}
    />
  );
}

/** A retry before the reply has a tool group to carry it: a row of its own. */
export function RetryStrip({ retry }: { retry: RetryStatus }) {
  return (
    <ExecutionStrip label="" status={<RetryTag retry={retry} lead={false} />}>
      <RetryStep retry={retry} />
    </ExecutionStrip>
  );
}
