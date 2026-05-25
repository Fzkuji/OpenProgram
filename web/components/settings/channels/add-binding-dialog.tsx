"use client";

import { useEffect, useState } from "react";

import styles from "./channels.module.css";
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
      setError("平台和 agent 都要选");
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
          <span className={styles.modalTitle}>添加派发规则</span>
          <button
            className={styles.iconBtn}
            onClick={onClose}
            type="button"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className={styles.modalBody}>
          <div className={styles.formHelp}>
            规则的意思: <b>"从 [平台] 上 [某个 bot 账号]、由 [某个用户/群]
            发来的消息, 都派给 [某个 agent] 处理。"</b> 留空的部分就是"任何"。
          </div>

          <label className={styles.formRow}>
            <span className={styles.formLabel}>① 哪个平台</span>
            <select
              className={styles.formInput}
              value={channel}
              onChange={(e) => {
                setChannel(e.target.value);
                setAccountId("");
              }}
            >
              {channelOptions.length === 0 ? (
                <option value="">(还没配机器人, 请先回到上面添加)</option>
              ) : null}
              {channelOptions.map((c) => (
                <option key={c} value={c}>
                  {PLATFORM_LABEL[c] || c}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.formRow}>
            <span className={styles.formLabel}>② 哪个 bot 账号</span>
            <select
              className={styles.formInput}
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
            >
              <option value="">任何 bot 账号</option>
              {accountOptions.map((a) => (
                <option key={a.account_id} value={a.account_id}>
                  {a.account_id}
                </option>
              ))}
            </select>
            <span className={styles.formHint}>
              留 "任何" 就匹配这个平台下所有 bot。
            </span>
          </label>

          <label className={styles.formRow}>
            <span className={styles.formLabel}>③ 谁发来的</span>
            <input
              className={styles.formInput}
              value={peer}
              onChange={(e) => setPeer(e.target.value)}
              placeholder="留空 = 任何人发来的都算"
            />
            <span className={styles.formHint}>
              想限定某个用户 / 群, 填它的 ID (比如 Telegram chat id、
              Discord channel id)。留空就匹配任何对方。
            </span>
          </label>

          {peer.trim() && (
            <label className={styles.formRow}>
              <span className={styles.formLabel}>对方类型</span>
              <select
                className={styles.formInput}
                value={peerKind}
                onChange={(e) => setPeerKind(e.target.value)}
              >
                <option value="direct">私聊 (1 对 1)</option>
                <option value="group">群组 (多人)</option>
                <option value="channel">频道 / 广播</option>
              </select>
            </label>
          )}

          <label className={styles.formRow}>
            <span className={styles.formLabel}>④ 派给哪个 agent</span>
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
                placeholder="agent id (如 main)"
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
            取消
          </button>
          <button
            className={styles.primaryBtn}
            onClick={submit}
            disabled={submitting}
            type="button"
          >
            {submitting ? "添加中…" : "添加规则"}
          </button>
        </div>
      </div>
    </div>
  );
}
