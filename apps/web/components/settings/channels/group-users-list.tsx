"use client";

import { jsonFetch } from "@/lib/net/fetch-client";
import { useState } from "react";

import styles from "./channels.module.css";
import { Button } from "@/components/ui/button";
import { PlusIcon } from "@/components/animated-icons";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { SimpleSelect } from "@/components/ui/select";
import { useTranslation } from "@/lib/i18n";
import { GROUP_PLATFORMS, PLATFORM_LABEL } from "./types";
import type { ChannelAccessAccount } from "./types";

interface Props {
  access: ChannelAccessAccount[];
  onChange: () => void | Promise<void>;
}

type GroupAccess = "full" | "chat";

function accountKey(account: ChannelAccessAccount): string {
  return `${account.channel}:${account.account_id}`;
}

export function GroupUsersList({ access, onChange }: Props) {
  const { text } = useTranslation();
  const [addOpen, setAddOpen] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const accounts = access.filter((account) => GROUP_PLATFORMS.includes(account.channel));
  if (accounts.length === 0) return null;

  const remove = async (account: ChannelAccessAccount, userId: string) => {
    const key = `${accountKey(account)}:${userId}`;
    setBusyKey(key);
    setError(null);
    try {
      const path = [account.channel, account.account_id, userId]
        .map(encodeURIComponent)
        .join("/");
      await jsonFetch(`/api/channels/access/group-users/${path}`, { method: "DELETE" });
      await onChange();
    } catch (e) {
      setError((e as Error).message || text("Remove failed", "移除失败"));
    } finally {
      setBusyKey(null);
    }
  };

  const rows = accounts.flatMap((account) => [
    ...account.group_full_access.map((user) => ({ account, user, level: "full" as GroupAccess })),
    ...account.group_chat_only.map((user) => ({ account, user, level: "chat" as GroupAccess })),
  ]);

  return (
    <>
      <div className="flex items-start justify-between gap-[16px]">
        <div>
          <div className={styles.sectionTitle}>
            {text("Step 3 - People in group chats", "步骤 3 - 群聊里的人")}
          </div>
          <div className={styles.sectionSub}>
            {text(
              "In a group, the Agent answers the paired senders above and the people listed here. Everyone else gets no reaction at all: no reply, no code. Full access runs as you, with every tool. Chat only gets replies and memory.",
              "在群里，Agent 只理会上面已配对的发送者和这里列出的人，其他人发什么都没有任何反应，不回复，也不发配对码。“完全访问”以你的身份运行，能用所有工具；“只能聊天”只能得到回复和记忆。",
            )}
          </div>
        </div>
        <Button variant="default" onClick={() => setAddOpen(true)} type="button">
          <PlusIcon size={16} aria-hidden />{text("Add person", "添加")}
        </Button>
      </div>

      {error && <div className={styles.formError} role="alert">{error}</div>}

      {rows.length === 0 ? (
        <div className={styles.emptyHint}>
          {text(
            "Nobody is listed. In groups, only paired senders get a response.",
            "还没有列出任何人。在群里，只有已配对的发送者会得到回应。",
          )}
        </div>
      ) : (
        <table className={styles.rowTable}>
          <thead>
            <tr>
              <th>{text("Bot account", "Bot 账号")}</th>
              <th>{text("Person", "人员")}</th>
              <th>{text("Access", "权限")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ account, user, level }) => {
              const key = `${accountKey(account)}:${user.user_id}`;
              return (
                <tr key={key}>
                  <td>{PLATFORM_LABEL[account.channel] || account.channel} · <code>{account.account_id}</code></td>
                  <td>{user.display && user.display !== user.user_id ? `${user.display} · ` : ""}<code>{user.user_id}</code></td>
                  <td>
                    <span className={level === "full" ? styles.badgeOk : styles.badgeMuted}>
                      {level === "full" ? text("full access", "完全访问") : text("chat only", "只能聊天")}
                    </span>
                  </td>
                  <td>
                    <Button
                      variant="destructive" size="xs"
                      disabled={busyKey !== null}
                      onClick={() => remove(account, user.user_id)}
                      aria-label={text(`Remove ${user.user_id}`, `移除 ${user.user_id}`)}
                      type="button"
                    >
                      {busyKey === key ? text("Removing...", "移除中...") : text("Remove", "移除")}
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {addOpen && (
        <AddGroupUserDialog
          accounts={accounts}
          onClose={() => setAddOpen(false)}
          onAdded={async () => {
            setAddOpen(false);
            await onChange();
          }}
        />
      )}
    </>
  );
}

function AddGroupUserDialog({
  accounts,
  onClose,
  onAdded,
}: {
  accounts: ChannelAccessAccount[];
  onClose: () => void;
  onAdded: () => void | Promise<void>;
}) {
  const { t, text } = useTranslation();
  const [selected, setSelected] = useState(accountKey(accounts[0]));
  const [userId, setUserId] = useState("");
  const [level, setLevel] = useState<GroupAccess>("chat");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const account = accounts.find((row) => accountKey(row) === selected);
    if (!account || !userId.trim()) {
      setError(text("A bot account and a user id are both required.", "Bot 账号和用户 id 都是必填项。"));
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await jsonFetch("/api/channels/access/group-users", {
        method: "POST",
        body: JSON.stringify({
          channel: account.channel,
          account_id: account.account_id,
          user_id: userId.trim(),
          access: level,
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
          <DialogTitle>{text("Add a person for group chats", "添加群聊里的人")}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-[14px] min-h-0 overflow-y-auto">
          <label className="flex flex-col gap-[6px]">
            <span className={styles.formLabel}>{text("Bot account", "Bot 账号")}</span>
            <SimpleSelect label={text("Bot account", "Bot 账号")}
              className="w-full"
              value={selected}
              onValueChange={setSelected}
              options={accounts.map((account) => ({
                value: accountKey(account),
                label: `${PLATFORM_LABEL[account.channel] || account.channel} · ${account.account_id}`,
              }))}
            />
          </label>

          <label className="flex flex-col gap-[6px]">
            <span className={styles.formLabel}>{text("User id", "用户 id")}</span>
            <Input value={userId} onChange={(e) => setUserId(e.target.value)} />
            <span className={styles.formHint}>
              {text(
                "The platform's user id, not a username: a number on Telegram and Discord, U... on Slack. Usernames can change, so they do not work here.",
                "填平台的用户 id，不是用户名：Telegram 和 Discord 是数字，Slack 以 U 开头。用户名可以改，这里不能用。",
              )}
            </span>
          </label>

          <label className="flex flex-col gap-[6px]">
            <span className={styles.formLabel}>{text("What they can do", "能做什么")}</span>
            <SimpleSelect label={text("What they can do", "能做什么")}
              className="w-full"
              value={level}
              onValueChange={(value) => setLevel(value as GroupAccess)}
              options={[
                { value: "chat", label: text("Chat only: replies and memory", "只能聊天：回复和记忆") },
                { value: "full", label: text("Full access: runs as you, with every tool", "完全访问：以你的身份运行，能用所有工具") },
              ]}
            />
          </label>

          {error && <div className={styles.formError}>{error}</div>}
        </div>

        <DialogFooter>
          <Button variant="elevated" onClick={onClose} type="button">
            {t("sidebar.cancel")}
          </Button>
          <Button onClick={submit} disabled={submitting} type="button">
            {submitting ? text("Adding...", "添加中...") : text("Add", "添加")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
