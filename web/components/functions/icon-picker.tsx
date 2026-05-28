"use client";

import { useEffect } from "react";
import styles from "./icon-picker.module.css";
import { useTranslation } from "@/lib/i18n";

export const DEFAULT_ICON = "📦";

export const ICON_CHOICES = [
  "📦", "🤖", "🌐", "🔍", "📚", "🖥",
  "📄", "📊", "🎨", "✏️", "🛠", "⚡",
  "💡", "🔥", "⭐", "🎯", "📷", "🎵",
  "🧠", "💬", "🎮", "🚀", "🧪", "✨",
];

export function IconPicker({
  name,
  current,
  onPick,
  onClose,
}: {
  name: string;
  current: string;
  onPick: (icon: string | null) => void;
  onClose: () => void;
}) {
  const { text } = useTranslation();
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className={styles.overlay} onClick={onClose}>
      <div className={styles.picker} onClick={(e) => e.stopPropagation()}>
        <div className={styles.head}>
          <span className={styles.title}>
            {text("Pick an icon for", "为以下函数选择图标")} <code>{name}</code>
          </span>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.btnGhost}
              onClick={() => onPick(null)}
              title={text("Reset to default", "恢复默认")}
            >
              {text("Reset", "重置")}
            </button>
            <button
              type="button"
              className={styles.btnGhost}
              onClick={onClose}
              title={text("Close", "关闭")}
            >
              {text("Close", "关闭")}
            </button>
          </div>
        </div>
        <div className={styles.grid}>
          {ICON_CHOICES.map((emoji) => (
            <button
              key={emoji}
              type="button"
              className={
                emoji === current
                  ? `${styles.btn} ${styles.btnActive}`
                  : styles.btn
              }
              onClick={() => onPick(emoji)}
            >
              {emoji}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
