"use client";

import { jsonFetch } from "@/lib/net/fetch-client";
import { useState } from "react";

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

  const submit = async () => {
    setError(null);
    if (isWeChat) {
      setError(
        text(
          "WeChat needs QR-code login, which the web UI does not render. Run in a terminal: openprogram channels accounts login wechat --id ",
          "WeChat 需要二维码登录，当前 Web UI 不渲染二维码。请在终端运行：openprogram channels accounts login wechat --id ",
        ) + accountId,
      );
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
            <div className={styles.emptyHint}>
              {text("WeChat uses QR-code login, which the web UI does not render. Run this in a terminal:", "WeChat 使用二维码登录，当前 Web UI 不渲染二维码。请在终端运行：")}
              <pre className={styles.codeBlock}>
                openprogram channels accounts login wechat --id {accountId || "default"}
              </pre>
              {text("After you scan, the new bot shows up in the list above automatically. No need to refresh this page.", "扫码后，新 bot 会自动显示在上方列表，无需刷新页面。")}
            </div>
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
          {!isWeChat && (
            <Button
              onClick={submit}
              disabled={submitting}
              type="button"
            >
              {submitting ? text("Adding...", "添加中...") : text("Add bot", "添加 bot")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
