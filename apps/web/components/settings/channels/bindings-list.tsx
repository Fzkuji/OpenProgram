"use client";

import { settingsSend } from "@/lib/settings/settings-request";
import { useState } from "react";

import styles from "./channels.module.css";
import { Button } from "@/components/ui/button";
import { PlusIcon } from "@/components/animated-icons";
import { useTranslation } from "@/lib/i18n";
import { AgentAvatarById } from "@/components/agents/agent-avatar";
import { AddBindingDialog } from "./add-binding-dialog";
import { PLATFORM_LABEL } from "./types";
import type { ChannelAccount, ChannelBinding } from "./types";
import { confirmAction } from "@/components/ui/confirm-dialog";

interface Props {
  bindings: ChannelBinding[];
  accounts: ChannelAccount[];
  onChange: () => void | Promise<void>;
}

export function BindingsList({ bindings, accounts, onChange }: Props) {
  const { text } = useTranslation();
  const [addOpen, setAddOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const handleDelete = async (binding_id: string) => {
    if (!(await confirmAction({
      title: text("Delete this dispatch rule?", "删除这条分发规则？"),
      confirmLabel: text("Delete", "删除"),
      destructive: true,
    }))) return;
    setBusyId(binding_id);
    try {
      const deleted = await settingsSend(`/api/channels/bindings/${encodeURIComponent(binding_id)}`, {
        method: "DELETE",
        failure: text("Delete failed", "删除失败"),
      });
      if (deleted !== null) await onChange();
    } finally {
      setBusyId(null);
    }
  };

  const describeMatch = (m: ChannelBinding["match"]): string => {
    const platform = PLATFORM_LABEL[m.channel || ""] || m.channel || text("any platform", "任意平台");
    const account = m.account_id
      ? text(`bot "${m.account_id}"`, `bot“${m.account_id}”`)
      : text("any bot", "任意 bot");
    let target: string;
    if (m.peer?.id) {
      const kind = m.peer.kind === "group" ? text("group", "群组") :
                   m.peer.kind === "channel" ? text("channel", "频道") : text("user", "用户");
      target = `${kind} ${m.peer.id}`;
    } else {
      target = text("any sender", "任意发送者");
    }
    return `${platform} · ${account} · ${target}`;
  };

  return (
    <>
      <div className="flex items-start justify-between gap-[16px]">
        <div>
          <div className={styles.sectionTitle}>{text("Step 4 - Dispatch rules", "步骤 4 - 分发规则")}</div>
          <div className={styles.sectionSub}>
            {text(
              "Decide which agent handles which incoming messages. Without any rule, every message goes to the default agent. Add a rule to route a specific platform / group / sender to a chosen agent. Most-specific rule wins.",
              "决定由哪个 Agent 处理哪些入站消息。没有规则时，所有消息都会进入默认 Agent。可添加规则，将特定平台 / 群组 / 发送者路由到指定 Agent。最具体的规则优先。",
            )}
          </div>
        </div>
        <Button
          variant="default"
          onClick={() => setAddOpen(true)}
          type="button"
        >
          <PlusIcon size={16} aria-hidden />{text("Add rule", "添加规则")}
        </Button>
      </div>

      {bindings.length === 0 ? (
        <div className={styles.emptyHint}>
          {text(
            "No rules yet. Every incoming message goes to the default agent. To route specific groups / users to a different agent, click \"Add rule\".",
            "还没有规则。所有入站消息都会进入默认 Agent。要把特定群组 / 用户路由到不同 Agent，请点击“添加规则”。",
          )}
        </div>
      ) : (
        <table className={styles.rowTable}>
          <thead>
            <tr>
              <th>{text("When a message looks like", "当消息匹配")}</th>
              <th>{text("Send it to agent", "发送到 Agent")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {bindings.map((b) => (
              <tr key={b.id}>
                <td>{describeMatch(b.match)}</td>
                <td><span className="inline-flex items-center gap-[6px]"><AgentAvatarById agentId={b.agent_id} size={16} /><code>{b.agent_id}</code></span></td>
                <td>
                  <Button
                    variant="destructive" size="xs"
                    onClick={() => handleDelete(b.id)}
                    disabled={busyId === b.id}
                    type="button"
                  >
                    {busyId === b.id ? text("Removing...", "移除中...") : text("Remove", "移除")}
                  </Button>
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
