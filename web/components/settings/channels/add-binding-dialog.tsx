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
      setError("channel 和 agent 都必填");
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
          <span className={styles.modalTitle}>Add binding</span>
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
          <label className={styles.formRow}>
            <span className={styles.formLabel}>Channel</span>
            <select
              className={styles.formInput}
              value={channel}
              onChange={(e) => {
                setChannel(e.target.value);
                setAccountId("");
              }}
            >
              {channelOptions.length === 0 ? (
                <option value="">(no channels configured)</option>
              ) : null}
              {channelOptions.map((c) => (
                <option key={c} value={c}>
                  {PLATFORM_LABEL[c] || c}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.formRow}>
            <span className={styles.formLabel}>Account (optional)</span>
            <select
              className={styles.formInput}
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
            >
              <option value="">— any account —</option>
              {accountOptions.map((a) => (
                <option key={a.account_id} value={a.account_id}>
                  {a.account_id}
                </option>
              ))}
            </select>
          </label>

          <label className={styles.formRow}>
            <span className={styles.formLabel}>Peer (optional)</span>
            <input
              className={styles.formInput}
              value={peer}
              onChange={(e) => setPeer(e.target.value)}
              placeholder="留空匹配任何 peer; 填写则只匹配该 peer"
            />
          </label>

          {peer.trim() && (
            <label className={styles.formRow}>
              <span className={styles.formLabel}>Peer kind</span>
              <select
                className={styles.formInput}
                value={peerKind}
                onChange={(e) => setPeerKind(e.target.value)}
              >
                <option value="direct">direct (1-on-1 DM)</option>
                <option value="group">group (multi-user)</option>
                <option value="channel">channel (broadcast)</option>
              </select>
            </label>
          )}

          <label className={styles.formRow}>
            <span className={styles.formLabel}>→ Agent</span>
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
                placeholder="agent id"
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
            Cancel
          </button>
          <button
            className={styles.primaryBtn}
            onClick={submit}
            disabled={submitting}
            type="button"
          >
            {submitting ? "Adding…" : "Add binding"}
          </button>
        </div>
      </div>
    </div>
  );
}
