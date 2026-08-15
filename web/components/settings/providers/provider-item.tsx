"use client";

import { ProviderIcon } from "../provider-icon";

import styles from "../settings-page.module.css";
import type { Provider } from "./types";
import { useTranslation } from "@/lib/i18n";

/** One row in the left sidebar — provider icon, label, and a colored
 *  status dot (green = enabled, yellow = configured-but-off, grey =
 *  unconfigured). */
export function ProviderItem({
  p,
  active,
  onSelect,
}: {
  p: Provider;
  active: boolean;
  onSelect: () => void;
}) {
  const { text } = useTranslation();
  const dot = p.enabled ? "on" : p.configured ? "off" : "unconfigured";
  return (
    <button
      type="button"
      className={styles.providerItem + (active ? " " + styles.active : "")}
      onClick={onSelect}
      title={p.label}
      aria-label={p.label}
      aria-current={active ? "true" : undefined}
    >
      <ProviderIcon id={p.id} size={20} />
      <span className={styles.providerLabel}>{p.label}</span>
      {p.custom && (
        <span
          className={styles.providerCustomBadge}
          title={text("Custom provider you added", "你添加的自定义 Provider")}
        >
          {text("Custom", "自定义")}
        </span>
      )}
      <span
        className={
          styles.providerDot +
          " " +
          (dot === "on" ? styles.on : dot === "off" ? styles.off : styles.unconfigured)
        }
        title={
          p.enabled ? text("Enabled", "已启用") : p.configured ? text("Not enabled", "未启用") : text("Not configured", "未配置")
        }
      />
    </button>
  );
}
