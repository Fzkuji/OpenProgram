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
    if (!confirm(`删除这条派发规则?`)) return;
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
    const platform = PLATFORM_LABEL[m.channel || ""] || m.channel || "任何平台";
    const account = m.account_id
      ? `账号 "${m.account_id}"`
      : "任何账号";
    let target: string;
    if (m.peer?.id) {
      const kind = m.peer.kind === "group" ? "群" :
                   m.peer.kind === "channel" ? "频道" : "用户";
      target = `${kind} ${m.peer.id}`;
    } else {
      target = "任何对方";
    }
    return `${platform} · ${account} · ${target}`;
  };

  return (
    <>
      <div className={styles.sectionHeader}>
        <div>
          <div className={styles.sectionTitle}>第 2 步: 派发规则</div>
          <div className={styles.sectionSub}>
            谁的消息派给哪个 agent? 没规则时所有消息都进默认 agent;
            加一条规则就可以让某个平台 / 某个群 / 某个用户的消息进指定
            的 agent。规则按"越具体越优先"匹配。
          </div>
        </div>
        <button
          className={styles.primaryBtn}
          onClick={() => setAddOpen(true)}
          type="button"
        >
          + 添加规则
        </button>
      </div>

      {bindings.length === 0 ? (
        <div className={styles.emptyHint}>
          还没有规则。所有进来的消息都会派给默认 agent 处理。如果想让
          某个群 / 某个用户的消息进特定的 agent, 点击"+ 添加规则"。
        </div>
      ) : (
        <table className={styles.rowTable}>
          <thead>
            <tr>
              <th>什么样的消息</th>
              <th>派给哪个 agent</th>
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
                    {busyId === b.id ? "删除中…" : "删除"}
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
