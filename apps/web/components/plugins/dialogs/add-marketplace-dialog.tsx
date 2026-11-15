"use client";

import { useState } from "react";
import styles from "../plugins.module.css";
import { usePluginsStore } from "@/lib/abilities/plugins-store";
import { useTranslation } from "@/lib/i18n";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface Props {
  onClose: () => void;
}

export function AddMarketplaceDialog({ onClose }: Props) {
  const { t, text } = useTranslation();
  const addMarketplace = usePluginsStore((s) => s.addMarketplace);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    setErr("");
    if (!url.trim()) {
      setErr(text("URL is required", "URL 不能为空"));
      return;
    }
    setBusy(true);
    try {
      await addMarketplace(url.trim(), name.trim());
      onClose();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className={styles.dialog} aria-describedby={undefined}>
        <DialogHeader><DialogTitle>{text("Add Marketplace", "添加 Marketplace")}</DialogTitle></DialogHeader>
        <div className={styles.dialogBody}>
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: "var(--fs-xs)", marginBottom: 4, color: "var(--text-secondary)" }}>{text("Index URL", "索引 URL")}</div>
            <Input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/plugins.json"
            />
          </div>
          <div style={{ marginBottom: 8 }}>
            <div style={{ fontSize: "var(--fs-xs)", marginBottom: 4, color: "var(--text-secondary)" }}>{text("Name (optional)", "名称（可选）")}</div>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          {err && <div style={{ color: "var(--error-form)", fontSize: "var(--fs-xs)" }}>{err}</div>}
        </div>
        <DialogFooter>
          <Button variant="elevated" onClick={onClose} disabled={busy}>{t("sidebar.cancel")}</Button>
          <Button onClick={submit} disabled={busy}>
            {busy ? text("Adding...", "添加中...") : text("Add", "添加")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
