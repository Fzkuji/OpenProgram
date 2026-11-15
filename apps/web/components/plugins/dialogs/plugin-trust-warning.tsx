"use client";

import { useState } from "react";
import styles from "../plugins.module.css";
import { usePluginsStore } from "@/lib/abilities/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

interface Props {
  name: string;
  currentLevel: string;
  onDone: () => void;
  onCancel: () => void;
}

export function PluginTrustWarning({ name, currentLevel, onDone, onCancel }: Props) {
  const { t, text } = useTranslation();
  const setTrust = usePluginsStore((s) => s.setTrust);
  const [level, setLevel] = useState<string>(
    currentLevel === "untrusted" ? "community" : currentLevel,
  );
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await setTrust(name, level);
      onDone();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onCancel(); }}>
      <DialogContent className={styles.dialog} aria-describedby={undefined}>
        <DialogHeader><DialogTitle>{text(`Raise trust level for ${name}`, `提升 ${name} 的 trust 等级`)}</DialogTitle></DialogHeader>
        <div className={styles.dialogBody}>
          <p>
            {text("Current level: ", "当前等级 ")}
            <strong>{currentLevel}</strong>
            {text(
              ". You must upgrade to community or verified before first enabling it. Verified loads in-process; community will use a subprocess sandbox in the future (currently still in-process).",
              "。首次启用前必须升级到 community 或 verified。verified 会以 in-process 方式加载，community 未来将走 subprocess 沙箱（当前仍 in-process）。",
            )}
          </p>
          <RadioGroup
            className="mt-3 flex gap-4"
            value={level}
            onValueChange={(value) => setLevel(value as typeof level)}
          >
            <label className="flex items-center gap-2">
              <RadioGroupItem value="community" />
              community
            </label>
            <label className="flex items-center gap-2">
              <RadioGroupItem value="verified" />
              verified
            </label>
          </RadioGroup>
        </div>
        <DialogFooter>
          <Button variant="elevated" onClick={onCancel} disabled={busy}>{t("sidebar.cancel")}</Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? text("Saving...", "保存中...") : text("Save", "保存")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
