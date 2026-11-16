"use client";

/**
 * The line a reply carries when another of the provider's credentials took
 * over before it started (docs/reference/design/providers/provider-management.html,
 * "When a credential fails"): "Switched to Work: Personal hit a rate limit".
 * One muted line per switch, over the reply; nothing to confirm.
 */
import type { CredentialNotice } from "@/lib/session-store/types";
import { useTranslation } from "@/lib/i18n";

type Text = ReturnType<typeof useTranslation>["text"];

export function credentialNoticeLine(notice: CredentialNotice, text: Text): string {
  const from = notice.from;
  if (!from) return text(`Switched to ${notice.to}`, `已换到 ${notice.to}`);
  switch (notice.cause) {
    case "rate_limit":
      return text(`Switched to ${notice.to}: ${from} hit a rate limit`, `已换到 ${notice.to}：${from} 触发了速率限制`);
    case "quota":
      return text(`Switched to ${notice.to}: ${from} used up its quota`, `已换到 ${notice.to}：${from} 额度用完了`);
    case "auth":
      return text(`Switched to ${notice.to}: ${from} was refused`, `已换到 ${notice.to}：${from} 被拒绝`);
    case "model":
      return text(`Switched to ${notice.to}: ${from} cannot use this model`, `已换到 ${notice.to}：${from} 不能用这个模型`);
    default:
      return text(`Switched to ${notice.to}: ${from} failed`, `已换到 ${notice.to}：${from} 出错了`);
  }
}

export function CredentialNotices({ notices }: { notices?: CredentialNotice[] }) {
  const { text } = useTranslation();
  if (!notices?.length) return null;
  return (
    <div className="flex flex-col gap-0.5 pb-1" data-credential-notices>
      {notices.map((notice, i) => (
        <p key={i} className="m-0 text-fs-xs text-text-muted" role="status">{credentialNoticeLine(notice, text)}</p>
      ))}
    </div>
  );
}
