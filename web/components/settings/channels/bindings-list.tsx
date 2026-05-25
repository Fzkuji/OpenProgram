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
    if (!confirm(`Delete binding ${binding_id}?`)) return;
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
    const parts: string[] = [];
    parts.push(PLATFORM_LABEL[m.channel || ""] || m.channel || "*");
    parts.push(m.account_id ? `acct: ${m.account_id}` : "any account");
    if (m.peer?.id) {
      parts.push(`peer: ${m.peer.kind || "?"}:${m.peer.id}`);
    } else {
      parts.push("any peer");
    }
    return parts.join(" · ");
  };

  return (
    <>
      <div className={styles.sectionHeader}>
        <span className={styles.sectionTitle}>Bindings</span>
        <button
          className={styles.primaryBtn}
          onClick={() => setAddOpen(true)}
          type="button"
        >
          + Add binding
        </button>
      </div>

      {bindings.length === 0 ? (
        <div className={styles.emptyHint}>
          No bindings yet. Inbound messages route to the default agent.
          Add a binding to send specific platforms / peers to a chosen
          agent.
        </div>
      ) : (
        <table className={styles.rowTable}>
          <thead>
            <tr>
              <th>Match</th>
              <th>→ Agent</th>
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
