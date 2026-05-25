"use client";

import { useState } from "react";

import styles from "./channels.module.css";
import { AddBindingDialog } from "./add-binding-dialog";
import { PLATFORM_LABEL } from "./types";
import type { ChannelAccount, ChannelBinding } from "./types";

interface Props {
  bindings: ChannelBinding[];
  accounts: ChannelAccount[];
  onChange: () => void | Promise<void>;
}

export function BindingsList({ bindings, accounts, onChange }: Props) {
  const [addOpen, setAddOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const handleDelete = async (binding_id: string) => {
    if (!confirm(`Delete this dispatch rule?`)) return;
    setBusyId(binding_id);
    try {
      const r = await fetch(
        `/api/channels/bindings/${encodeURIComponent(binding_id)}`,
        { method: "DELETE" },
      );
      if (!r.ok) {
        const d = await r.json().catch(() => null);
        alert(d?.detail || "Delete failed");
      } else {
        await onChange();
      }
    } finally {
      setBusyId(null);
    }
  };

  const describeMatch = (m: ChannelBinding["match"]): string => {
    const platform = PLATFORM_LABEL[m.channel || ""] || m.channel || "any platform";
    const account = m.account_id
      ? `bot "${m.account_id}"`
      : "any bot";
    let target: string;
    if (m.peer?.id) {
      const kind = m.peer.kind === "group" ? "group" :
                   m.peer.kind === "channel" ? "channel" : "user";
      target = `${kind} ${m.peer.id}`;
    } else {
      target = "any sender";
    }
    return `${platform} · ${account} · ${target}`;
  };

  return (
    <>
      <div className={styles.sectionHeader}>
        <div>
          <div className={styles.sectionTitle}>Step 2 — Dispatch rules</div>
          <div className={styles.sectionSub}>
            Decide which agent handles which incoming messages. Without
            any rule, every message goes to the default agent. Add a
            rule to route a specific platform / group / sender to a
            chosen agent. Most-specific rule wins.
          </div>
        </div>
        <button
          className={styles.primaryBtn}
          onClick={() => setAddOpen(true)}
          type="button"
        >
          + Add rule
        </button>
      </div>

      {bindings.length === 0 ? (
        <div className={styles.emptyHint}>
          No rules yet. Every incoming message goes to the default agent.
          To route specific groups / users to a different agent, click
          "+ Add rule".
        </div>
      ) : (
        <table className={styles.rowTable}>
          <thead>
            <tr>
              <th>When a message looks like</th>
              <th>Send it to agent</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {bindings.map((b) => (
              <tr key={b.id}>
                <td>{describeMatch(b.match)}</td>
                <td><code>{b.agent_id}</code></td>
                <td>
                  <button
                    className={styles.dangerBtn}
                    onClick={() => handleDelete(b.id)}
                    disabled={busyId === b.id}
                    type="button"
                  >
                    {busyId === b.id ? "Removing…" : "Remove"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {addOpen && (
        <AddBindingDialog
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
