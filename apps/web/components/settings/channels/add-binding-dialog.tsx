"use client";

import { jsonFetch } from "@/lib/net/fetch-client";
import { useEffect, useState } from "react";

import styles from "./channels.module.css";
import { SimpleSelect } from "@/components/ui/select";
import { useTranslation } from "@/lib/i18n";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { PLATFORM_LABEL } from "./types";
import type { ChannelAccount } from "./types";
import { Input } from "@/components/ui/input";

interface Agent {
  id: string;
  name?: string;
}

interface Props {
  accounts: ChannelAccount[];
  onClose: () => void;
  onAdded: () => void | Promise<void>;
}

export function AddBindingDialog({ accounts, onClose, onAdded }: Props) {
  const { t, text } = useTranslation();
  const channelOptions = Array.from(
    new Set(accounts.map((a) => a.channel)),
  );
  const [channel, setChannel] = useState<string>(channelOptions[0] || "");
  const [accountId, setAccountId] = useState<string>("");
  const [peer, setPeer] = useState("");
  const [peerKind, setPeerKind] = useState("direct");
  const [agentId, setAgentId] = useState("main");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const r = await fetch("/api/agents");
        const d = await r.json();
        const list: Agent[] = Array.isArray(d) ? d : d.agents || [];
        setAgents(list);
      } catch {
        // ignore
      }
    })();
  }, []);

  const accountOptions = accounts.filter((a) => a.channel === channel);

  const submit = async () => {
    setError(null);
    if (!channel || !agentId.trim()) {
      setError(text("Platform and agent are both required.", "平台和 Agent 都是必填项。"));
      return;
    }
    setSubmitting(true);
    try {
      const body: Record<string, string> = {
        agent_id: agentId.trim(),
        channel,
      };
      if (accountId) body.account_id = accountId;
      if (peer.trim()) {
        body.peer = peer.trim();
        body.peer_kind = peerKind;
      }
      await jsonFetch("/api/channels/bindings", { method: "POST", body: JSON.stringify(body) });
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
          <DialogTitle>{text("Add a dispatch rule", "添加分发规则")}</DialogTitle>
        </DialogHeader>

        <div className="flex flex-col gap-[14px] min-h-0 overflow-y-auto">
          <div className={styles.formHelp}>
            {text("How a rule reads: ", "规则含义：")}
            <b>
              {text(
                "\"messages on [platform] via [bot], sent by [user/group], go to [agent].\"",
                "“来自 [platform]，经由 [bot]，由 [user/group] 发送的消息，进入 [agent]。”",
              )}
            </b>
            {text(" Any field left blank means \"match anything\".", " 留空字段表示“匹配任意值”。")}
          </div>

          <label className="flex flex-col gap-[6px]">
            <span className={styles.formLabel}>① {text("Platform", "平台")}</span>
            <SimpleSelect label={text("Platform", "平台")}
              className="w-full"
              value={channel}
              onValueChange={(value) => {
                setChannel(value);
                setAccountId("");
              }}
              options={[
                ...(channelOptions.length === 0
                  ? [{ value: "", label: text("(no bots configured; add one above first)", "（还没有配置 bot，请先在上方添加）") }]
                  : []),
                ...channelOptions.map((c) => ({ value: c, label: PLATFORM_LABEL[c] || c })),
              ]}
            />
          </label>

          <label className="flex flex-col gap-[6px]">
            <span className={styles.formLabel}>② {text("Which bot", "哪个 bot")}</span>
            <SimpleSelect label={text("Which bot", "哪个 bot")}
              className="w-full"
              value={accountId}
              onValueChange={setAccountId}
              options={[
                { value: "", label: text("any bot", "任意 bot") },
                ...accountOptions.map((a) => ({ value: a.account_id, label: a.account_id })),
              ]}
            />
            <span className={styles.formHint}>
              {text("Leave as \"any bot\" to match every bot on this platform.", "保持“任意 bot”即可匹配这个平台上的所有 bot。")}
            </span>
          </label>

          <label className="flex flex-col gap-[6px]">
            <span className={styles.formLabel}>③ {text("Sent by whom", "发送者")}</span>
            <Input
              value={peer}
              onChange={(e) => setPeer(e.target.value)}
              placeholder={text("leave blank = anyone", "留空 = 任意人")}
            />
            <span className={styles.formHint}>
              {text(
                "To restrict to one user / group, paste their id (Telegram chat id, Discord channel id, etc.). Blank matches anyone.",
                "要限制到某个用户 / 群组，请粘贴对应 id（Telegram chat id、Discord channel id 等）。留空表示匹配任意人。",
              )}
            </span>
          </label>

          {peer.trim() && (
            <label className="flex flex-col gap-[6px]">
              <span className={styles.formLabel}>{text("Sender type", "发送者类型")}</span>
              <SimpleSelect label={text("Sender type", "发送者类型")}
                className="w-full"
                value={peerKind}
                onValueChange={setPeerKind}
                options={[
                  { value: "direct", label: text("direct chat (1-on-1)", "私聊（一对一）") },
                  { value: "group", label: text("group (multi-user)", "群组（多人）") },
                  { value: "channel", label: text("channel / broadcast", "频道 / 广播") },
                ]}
              />
            </label>
          )}

          <label className="flex flex-col gap-[6px]">
            <span className={styles.formLabel}>④ {text("Send to agent", "发送到 Agent")}</span>
            {agents.length > 0 ? (
              <SimpleSelect label={text("Send to agent", "发送到 Agent")}
                className="w-full"
                value={agentId}
                onValueChange={setAgentId}
                options={agents.map((a) => ({ value: a.id, label: a.name ? `${a.id} (${a.name})` : a.id }))}
              />
            ) : (
              <Input
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                placeholder={text("agent id (e.g. main)", "agent id（例如 main）")}
              />
            )}
          </label>

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
          <Button
            onClick={submit}
            disabled={submitting}
            type="button"
          >
            {submitting ? text("Adding...", "添加中...") : text("Add rule", "添加规则")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
