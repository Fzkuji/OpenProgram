"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useTranslation } from "@/lib/i18n";

import styles from "../settings-page.module.css";
import type { Provider } from "./types";

/** claude-code only: pin which Meridian account (profile) this provider
 *  uses. Meridian (the local Claude proxy) can hold several Claude
 *  subscriptions as named profiles; pinning one here decouples
 *  OpenProgram's Claude account from whatever the terminal
 *  `claude auth login` last logged in. Empty = follow Meridian's
 *  default / the keychain login. Adding an account (browser login) is
 *  done with `meridian profile add <name>` in a terminal. */
export function MeridianProfile({
  provider,
  onChanged,
}: {
  provider: Provider;
  onChanged: () => void;
}) {
  const { text } = useTranslation();
  const [value, setValue] = useState(provider.meridian_profile || "");
  const [saved, setSaved] = useState(false);

  async function save() {
    try {
      await fetch(`/api/providers/${encodeURIComponent(provider.id)}/config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ meridian_profile: value.trim() }),
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      onChanged();
    } catch { /* ignore */ }
  }

  return (
    <div className={styles.detailSection}>
      <div className={styles.detailSectionTitle}>
        <span>{text("Meridian account (profile)", "Meridian 账号（profile）")}</span>
        <span className={styles.modelCountSummary}>
          {value
            ? text(`pinned: ${value}`, `已绑定：${value}`)
            : text("follows Meridian default", "跟随 Meridian 默认")}
        </span>
      </div>
      <div className={styles.detailRow}>
        <Input
          className="flex-1 font-mono"
          type="text"
          placeholder={text("e.g. experiment (empty = default)", "如 experiment（留空=默认）")}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button size="sm" onClick={save}>
          {saved ? text("Saved", "已保存") : text("Save", "保存")}
        </Button>
      </div>
      <div style={{ fontSize: "0.75rem", opacity: 0.6, marginTop: "0.4rem", lineHeight: 1.5 }}>
        {text(
          "Pins OpenProgram's Claude account to this Meridian profile, independent of the terminal `claude auth login`. Takes effect on the next request. Add an account with `meridian profile add <name>` (browser login) first.",
          "把 OpenProgram 用的 Claude 账号固定到这个 Meridian profile，与终端 `claude auth login` 无关，下一个请求即生效。先用 `meridian profile add <名字>`（浏览器登录）建好账号。",
        )}
      </div>
    </div>
  );
}
