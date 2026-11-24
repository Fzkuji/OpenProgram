"use client";

import { jsonFetch } from "@/lib/net/fetch-client";
import { useEffect, useRef, useState } from "react";
import { getSocket } from "@/lib/runtime-bridge/state";
import { QrCode } from "@/components/ui/qr-code";

import styles from "./channels.module.css";
import { SimpleSelect } from "@/components/ui/select";
import { useTranslation } from "@/lib/i18n";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { PLATFORMS, PLATFORM_LABEL } from "./types";
import { Input } from "@/components/ui/input";

interface Props {
  onClose: () => void;
  onAdded: () => void | Promise<void>;
}

const TOKEN_HINT: Record<string, string> = {
  telegram: "Open Telegram, message @BotFather with /newbot, it will hand you a token.",
  discord: "Discord Developer Portal → your Application → Bot → Reset Token.",
  slack: "Your Slack app settings → OAuth & Permissions → Bot User OAuth Token (starts with xoxb-).",
};

const TOKEN_HINT_ZH: Record<string, string> = {
  telegram: "打开 Telegram，向 @BotFather 发送 /newbot，它会返回 token。",
  discord: "Discord Developer Portal → 你的 Application → Bot → Reset Token。",
  slack: "你的 Slack app 设置 → OAuth & Permissions → Bot User OAuth Token（以 xoxb- 开头）。",
};

export function AddAccountDialog({ onClose, onAdded }: Props) {
  const { t, text } = useTranslation();
  const [channel, setChannel] = useState<string>("telegram");
  const [accountId, setAccountId] = useState("default");
  const [token, setToken] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isWeChat = channel === "wechat";
  const wechat = useWeChatLogin(onAdded);

  const submit = async () => {
    setError(null);
    if (isWeChat) {
      if (!accountId.trim()) {
        setError(text("Give the bot a name first.", "请先给 bot 命名。"));
        return;
      }
      wechat.start(accountId.trim());
      return;
    }
    if (!accountId.trim() || !token.trim()) {
      setError(text("Account name and bot token are both required.", "账号名称和 bot token 都是必填项。"));
      return;
    }
    setSubmitting(true);
    try {
      await jsonFetch("/api/channels/accounts", {
        method: "POST",
        body: JSON.stringify({
          channel,
          account_id: accountId.trim(),
          token: token.trim(),
        }),
      });
      await onAdded();
    } catch (e) {
      setError((e as Error).message);
      setSubmitting(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className={styles.modal} aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>{text("Add a bot", "添加 bot")}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-[14px] min-h-0 overflow-y-auto">
          <label className="flex flex-col gap-[6px]">
            <span className={styles.formLabel}>{text("Platform", "平台")}</span>
            <SimpleSelect label={text("Platform", "平台")}
              className="w-full"
              value={channel}
              onValueChange={setChannel}
              options={PLATFORMS.map((p) => ({ value: p, label: PLATFORM_LABEL[p] || p }))}
            />
          </label>

          <label className="flex flex-col gap-[6px]">
            <span className={styles.formLabel}>{text("Give it a name", "给它命名")}</span>
            <Input
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              placeholder={text("e.g. default / work / personal", "例如 default / work / personal")}
            />
            <span className={styles.formHint}>
              {text(
                "You can hold multiple bots per platform. This short name tells them apart in the list above.",
                "每个平台可以有多个 bot。这个短名称用于在上方列表中区分它们。",
              )}
            </span>
          </label>

          {isWeChat ? (
            wechat.state.phase === "idle" ? (
              <span className={styles.formHint}>
                {text(
                  "WeChat signs in by QR code: get one, then scan it with WeChat on your phone.",
                  "微信用二维码登录：获取二维码后，用手机微信扫码。",
                )}
              </span>
            ) : (
              <WeChatPanel state={wechat.state} onRetry={() => wechat.start(accountId.trim())} />
            )
          ) : (
            <label className="flex flex-col gap-[6px]">
              <span className={styles.formLabel}>Bot token</span>
              <Input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder={text("paste here", "粘贴到这里")}
                autoComplete="off"
              />
              <span className={styles.formHint}>
                {text(TOKEN_HINT[channel] || "", TOKEN_HINT_ZH[channel] || "")}
              </span>
            </label>
          )}

          {error && <div className={styles.formError}>{error}</div>}
        </div>

        <DialogFooter>
          <Button
            variant="elevated"
            onClick={onClose}
            type="button"
          >
            {t("sidebar.cancel")}
          </Button>
          {!isWeChat ? (
            <Button
              onClick={submit}
              disabled={submitting}
              type="button"
            >
              {submitting ? text("Adding...", "添加中...") : text("Add bot", "添加 bot")}
            </Button>
          ) : wechat.state.phase === "idle" ? (
            <Button onClick={submit} type="button">{text("Get QR code", "获取二维码")}</Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type WeChatPhase = "idle" | "starting" | "qr_ready" | "scanned" | "confirmed" | "done" | "expired" | "error";

interface WeChatState {
  phase: WeChatPhase;
  url: string;
  message: string;
}

/** The QR login the worker runs (`start_channel_login`): its `qr_login` frames for this bot, phase by phase. */
function useWeChatLogin(onAdded: () => void | Promise<void>) {
  const [state, setState] = useState<WeChatState>({ phase: "idle", url: "", message: "" });
  const stop = useRef<(() => void) | null>(null);
  const done = useRef(onAdded);
  done.current = onAdded;

  useEffect(() => () => stop.current?.(), []);

  function start(accountId: string) {
    stop.current?.();
    const ws = getSocket();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      setState({ phase: "error", url: "", message: "" });
      return;
    }
    const onMessage = (event: MessageEvent) => {
      let frame: { type?: string; data?: Record<string, unknown> };
      try {
        frame = JSON.parse(String(event.data));
      } catch {
        return;
      }
      const data = frame?.data;
      if (frame?.type !== "qr_login" || !data || data.account_id !== accountId) return;
      const phase = String(data.phase || "") as WeChatPhase;
      setState((before) => ({
        phase,
        url: typeof data.url === "string" ? data.url : before.url,
        message: typeof data.message === "string" ? data.message : "",
      }));
      if (phase === "done") {
        stop.current?.();
        void done.current();
      }
      if (phase === "expired" || phase === "error") stop.current?.();
    };
    ws.addEventListener("message", onMessage);
    stop.current = () => {
      ws.removeEventListener("message", onMessage);
      stop.current = null;
    };
    setState({ phase: "starting", url: "", message: "" });
    ws.send(JSON.stringify({ action: "start_channel_login", channel: "wechat", account_id: accountId }));
  }

  return { state, start };
}

function WeChatPanel({ state, onRetry }: { state: WeChatState; onRetry: () => void }) {
  const { text } = useTranslation();
  const { phase, url } = state;
  const faded = phase === "scanned" || phase === "confirmed" || phase === "expired" || phase === "error";
  const [title, hint] = phase === "scanned" || phase === "confirmed"
    ? [text("Scanned", "已扫码"), text("Confirm the login on your phone.", "请在手机上确认登录。")]
    : phase === "done"
      ? [text("Connected", "已连接"), text("The bot is added and running.", "bot 已添加并运行。")]
      : phase === "expired" || phase === "error"
        ? [text("This code has expired", "二维码已失效"), text("Get a new one to try again.", "换一个新的再试。")]
        : [text("Scan with WeChat", "用微信扫码"), text("Open WeChat on your phone and scan this code.", "打开手机微信，扫描这个二维码。")];
  return (
    <div className="flex items-center gap-5 rounded-lg border bg-muted/40 p-4">
      <div className="relative w-[168px] shrink-0">
        {url ? (
          <QrCode
            value={url}
            label={text("WeChat login QR code", "微信登录二维码")}
            className={`w-full border p-2 ${faded ? "opacity-20 blur-[2px]" : ""}`}
          />
        ) : (
          <div className="aspect-square w-full rounded-md border bg-white" aria-hidden="true" />
        )}
        {phase === "expired" || phase === "error" ? (
          <Button className="absolute inset-0 m-auto w-fit" onClick={onRetry} type="button">
            {text("Get new code", "换新码")}
          </Button>
        ) : null}
      </div>
      <div className="flex min-w-0 flex-col gap-1">
        <span className="text-fs-base font-medium text-text-primary">{title}</span>
        <span className="text-fs-sm text-text-muted">{hint}</span>
      </div>
    </div>
  );
}
