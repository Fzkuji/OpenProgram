"use client";

import { useEffect, useState } from "react";

import styles from "./channels.module.css";
import { useTranslation } from "@/lib/i18n";
import { PLATFORM_LABEL } from "./types";
import type { ChannelAccount } from "./types";

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
      const r = await fetch("/api/channels/bindings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => null);
        setError(d?.detail || `HTTP ${r.status}`);
        setSubmitting(false);
        return;
      }
      await onAdded();
    } catch (e) {
      setError(String(e));
      setSubmitting(false);
    }
  };

  return (
    <div className={styles.modalBackdrop} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalHeader}>
          <span className={styles.modalTitle}>{text("Add a dispatch rule", "添加分发规则")}</span>
          <button
            className={styles.iconBtn}
            onClick={onClose}
            type="button"
            aria-label={text("Close", "关闭")}
          >
            ×
          </button>
        </div>

        <div className={styles.modalBody}>
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

          <label className={styles.formRow}>
            <span className={styles.formLabel}>① {text("Platform", "平台")}</span>
            <select
              className={styles.formInput}
              value={channel}
              onChange={(e) => {
                setChannel(e.target.value);
                setAccountId("");
              }}
            >
              {channelOptions.length === 0 ? (
                <option value="">{text("(no bots configured; add one above first)", "（还没有配置 bot，请先在上方添加）")}</option>
              ) : null}
              {channelOptions.map((c) => (
                <option key={c} value={c}>
                  {PLATFORM_LABEL[c] || c}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.formRow}>
            <span className={styles.formLabel}>② {text("Which bot", "哪个 bot")}</span>
            <select
              className={styles.formInput}
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
            >
              <option value="">{text("any bot", "任意 bot")}</option>
              {accountOptions.map((a) => (
                <option key={a.account_id} value={a.account_id}>
                  {a.account_id}
                </option>
              ))}
            </select>
            <span className={styles.formHint}>
              {text("Leave as \"any bot\" to match every bot on this platform.", "保持“任意 bot”即可匹配这个平台上的所有 bot。")}
            </span>
          </label>

          <label className={styles.formRow}>
            <span className={styles.formLabel}>③ {text("Sent by whom", "发送者")}</span>
            <input
              className={styles.formInput}
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
            <label className={styles.formRow}>
              <span className={styles.formLabel}>{text("Sender type", "发送者类型")}</span>
              <select
                className={styles.formInput}
                value={peerKind}
                onChange={(e) => setPeerKind(e.target.value)}
              >
                <option value="direct">{text("direct chat (1-on-1)", "私聊（一对一）")}</option>
                <option value="group">{text("group (multi-user)", "群组（多人）")}</option>
                <option value="channel">{text("channel / broadcast", "频道 / 广播")}</option>
              </select>
            </label>
          )}

          <label className={styles.formRow}>
            <span className={styles.formLabel}>④ {text("Send to agent", "发送到 Agent")}</span>
            {agents.length > 0 ? (
              <select
                className={styles.formInput}
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
              >
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name ? `${a.id} (${a.name})` : a.id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className={styles.formInput}
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                placeholder={text("agent id (e.g. main)", "agent id（例如 main）")}
              />
            )}
          </label>

          {error && <div className={styles.formError}>{error}</div>}
        </div>

        <div className={styles.modalFooter}>
          <button
            className={styles.secondaryBtn}
            onClick={onClose}
            type="button"
          >
            {t("sidebar.cancel")}
          </button>
          <button
            className={styles.primaryBtn}
            onClick={submit}
            disabled={submitting}
            type="button"
          >
            {submitting ? text("Adding...", "添加中...") : text("Add rule", "添加规则")}
          </button>
        </div>
      </div>
    </div>
  );
}
