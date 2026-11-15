"use client";

import { useCallback, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ExternalLink, KeyRound, XCircle } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { useTranslation } from "@/lib/i18n";
import type { KeyCheck } from "@/lib/settings/provider-status.generated";

import { fmtAmount } from "../status/model";
import styles from "../status/status.module.css";
import type { Stored } from "./data";

/** Results that keep the key and let setup go on (key_check.ENABLING_RESULTS). */
export const USABLE = new Set(["works", "no_credit", "unverified"]);

type Outcome =
  | { kind: "stored"; stored: Stored }
  | { kind: "rejected"; check: KeyCheck | null; message: string }
  | { kind: "failed"; message: string };

function External({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children} <ExternalLink size={12} aria-hidden="true" style={{ display: "inline", verticalAlign: "-1px" }} />
    </a>
  );
}

/** The one line a key check answers with (design §4, step 2). */
export function CheckLine({
  check, consoleUrl, host, onRetry, retrying = false,
}: {
  check: KeyCheck;
  consoleUrl: string | null;
  host: string;
  onRetry?: () => void;
  retrying?: boolean;
}) {
  const { text } = useTranslation();
  const keysUrl = (check.fix_page === "keys" ? check.fix_url : null) ?? consoleUrl;
  const billingUrl = check.fix_page === "billing" ? check.fix_url : null;
  switch (check.result) {
    case "works": {
      const parts = [
        check.balance ? fmtAmount(check.balance.amount, check.balance.unit) : null,
        check.model_count ? text(`${check.model_count} models`, `${check.model_count} 个模型`) : null,
      ].filter(Boolean);
      return (
        <div className={`${styles.line} ${styles.ok}`} role="status" data-result="works">
          <CheckCircle2 aria-hidden="true" />
          <span>{text("Key works", "密钥可用")}</span>
          {parts.map((part) => (
            <span key={part} className={styles.num}><span className={styles.muted}>· </span>{part}</span>
          ))}
        </div>
      );
    }
    case "unverified":
      return (
        <div className={`${styles.line} ${styles.wait}`} role="status" data-result="unverified">
          <CheckCircle2 aria-hidden="true" />
          <span>{text("Key saved · not verified", "密钥已保存 · 未能验证")}</span>
        </div>
      );
    case "no_credit":
      return (
        <div className={`${styles.line} ${styles.warn}`} role="status" data-result="no_credit">
          <AlertTriangle aria-hidden="true" />
          <span>{text("Key works · no credit", "密钥可用 · 余额不足")}</span>
          <span className="flex-1" />
          {billingUrl ? <External href={billingUrl}>{text("Top up", "充值")}</External> : null}
        </div>
      );
    case "not_available":
      return (
        <div className={`${styles.line} ${styles.warn}`} role="alert" data-result="not_available">
          <AlertTriangle aria-hidden="true" />
          <span>{text(`Not available in your region or plan (${check.http_status ?? 403})`, `你所在地区或套餐不可用（${check.http_status ?? 403}）`)}</span>
        </div>
      );
    case "unreachable":
      return (
        <div className={`${styles.line} ${styles.wait}`} role="alert" data-result="unreachable">
          {retrying ? <Spinner /> : <AlertTriangle aria-hidden="true" />}
          <span>{host ? text(`Can't reach ${host}`, `无法连接 ${host}`) : text("Can't reach the provider", "无法连接服务商")}</span>
          <span className="flex-1" />
          {onRetry ? <button type="button" className={styles.linkButton} onClick={onRetry} disabled={retrying}>{text("Retry", "重试")}</button> : null}
        </div>
      );
    case "rejected":
    default:
      return (
        <div className={`${styles.line} ${styles.bad}`} role="alert" data-result="rejected">
          <XCircle aria-hidden="true" />
          <span>{text(`Key rejected (${check.http_status ?? 401})`, `密钥被拒绝（${check.http_status ?? 401}）`)}</span>
          <span className="flex-1" />
          {keysUrl ? <External href={keysUrl}>{text("Get a key", "获取密钥")}</External> : null}
        </div>
      );
  }
}

/** One masked key field, checked when pasted, on Enter or when it loses
 *  focus. A kept key reports through `onStored`; a rejected one is not
 *  stored and says so in one line. The key itself never reaches the page
 *  again: the field only holds what was typed. */
export function KeyEntry({
  providerId, consoleUrl, host, name, onStored, autoFocus = false,
}: {
  providerId: string;
  consoleUrl: string | null;
  host: string;
  /** Account name for a second key; blank lets the Worker pick one. */
  name?: string;
  onStored: (stored: Stored) => void;
  autoFocus?: boolean;
}) {
  const { text } = useTranslation();
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const checked = useRef<string>("");

  const submit = useCallback(async (raw: string) => {
    const key = raw.trim();
    if (!key || busy || key === checked.current) return;
    checked.current = key;
    setBusy(true);
    setOutcome(null);
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(providerId)}/accounts/keys`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key, validate: true, ...(name ? { name } : {}) }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        const stored: Stored = { name: String(d.name ?? ""), check: d.check ?? null };
        setOutcome({ kind: "stored", stored });
        onStored(stored);
      } else if (r.status === 409) {
        setOutcome({ kind: "failed", message: text("A key with this name already exists", "已有同名密钥") });
      } else {
        setOutcome({ kind: "rejected", check: d.check ?? null, message: String(d.error ?? "") });
      }
    } catch {
      checked.current = "";
      setOutcome({ kind: "failed", message: text("Couldn't save the key", "无法保存密钥") });
    } finally {
      setBusy(false);
    }
  }, [busy, name, onStored, providerId, text]);

  const retry = useCallback(async () => {
    if (outcome?.kind !== "stored") return;
    setRetrying(true);
    try {
      const r = await fetch(`/api/providers/${encodeURIComponent(providerId)}/accounts/${encodeURIComponent(outcome.stored.name)}/validate`, { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (d.check) {
        const stored = { ...outcome.stored, check: d.check as KeyCheck };
        setOutcome({ kind: "stored", stored });
        onStored(stored);
      }
    } finally {
      setRetrying(false);
    }
  }, [onStored, outcome, providerId]);

  const kept = outcome?.kind === "stored";
  const rejected = outcome?.kind === "rejected";
  return (
    <div className="flex flex-col gap-[10px]">
      <div className={`${styles.keyField} flex items-center gap-[8px]`}>
        <KeyRound size={16} className={styles.muted} aria-hidden="true" />
        <Input
          type="password"
          className={styles.keyInput}
          autoComplete="off"
          spellCheck={false}
          autoFocus={autoFocus}
          aria-label={text("API key", "API 密钥")}
          aria-invalid={rejected || undefined}
          placeholder={text("Paste an API key", "粘贴 API 密钥")}
          value={value}
          readOnly={kept}
          onChange={(e) => { setValue(e.target.value); if (outcome?.kind !== "stored") setOutcome(null); }}
          onPaste={(e) => {
            const pasted = e.clipboardData?.getData("text") ?? "";
            if (pasted.trim()) setTimeout(() => { void submit(pasted); }, 0);
          }}
          onBlur={(e) => { void submit(e.currentTarget.value); }}
          onKeyDown={(e) => { if (e.key === "Enter") void submit(e.currentTarget.value); }}
        />
      </div>
      {busy ? (
        <div className={`${styles.line} ${styles.wait}`} role="status"><Spinner /><span>{text("Checking the key…", "正在检查密钥…")}</span></div>
      ) : outcome?.kind === "stored" && outcome.stored.check ? (
        <CheckLine check={outcome.stored.check} consoleUrl={consoleUrl} host={host} onRetry={retry} retrying={retrying} />
      ) : outcome?.kind === "stored" ? (
        <div className={`${styles.line} ${styles.wait}`} role="status"><CheckCircle2 aria-hidden="true" /><span>{text("Key saved", "密钥已保存")}</span></div>
      ) : outcome?.kind === "rejected" ? (
        outcome.check ? <CheckLine check={outcome.check} consoleUrl={consoleUrl} host={host} /> : (
          <div className={`${styles.line} ${styles.bad}`} role="alert"><XCircle aria-hidden="true" /><span>{outcome.message || text("Key rejected", "密钥被拒绝")}</span></div>
        )
      ) : outcome?.kind === "failed" ? (
        <div className={`${styles.line} ${styles.bad}`} role="alert">
          <XCircle aria-hidden="true" /><span>{outcome.message}</span><span className="flex-1" />
          <button type="button" className={styles.linkButton} onClick={() => { checked.current = ""; void submit(value); }}>{text("Retry", "重试")}</button>
        </div>
      ) : consoleUrl ? (
        <div className={`${styles.line} ${styles.wait}`}>
          <span className="flex-1" />
          <External href={consoleUrl}>{text("Get a key", "获取密钥")}</External>
        </div>
      ) : null}
    </div>
  );
}
