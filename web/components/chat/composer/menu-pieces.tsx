/**
 * Small visual pieces used by the Composer's bottom row — active-tool
 * chip + plus-menu row. Behaviour stays in Composer; these are pure
 * presentation.
 */
"use client";

import type { ReactNode } from "react";

import styles from "./composer.module.css";
import { CheckIcon, ChipCloseIcon } from "./icons";

export function ToolChip({
  icon,
  label,
  onRemove,
}: {
  icon: ReactNode;
  label: string;
  onRemove: () => void;
}) {
  return (
    <div
      className={styles.toolChip}
      onClick={onRemove}
      data-tooltip={label}
      title=""
    >
      <span className={styles.toolChipIcon}>{icon}</span>
      <span className={styles.toolChipClose} aria-label="Remove">
        <ChipCloseIcon />
      </span>
    </div>
  );
}

export function PlusMenuItem({
  active,
  onClick,
  icon,
  label,
  title,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  label: string;
  title?: string;
}) {
  return (
    <div
      className={`${styles.plusMenuItem} ${active ? styles.active : ""}`}
      onClick={onClick}
      title={title}
    >
      <div className={styles.plusMenuLeft}>
        <span className={styles.plusMenuIcon}>{icon}</span>
        <span className={styles.plusMenuLabel}>{label}</span>
      </div>
      <div className={styles.plusMenuRight}>{active && <CheckIcon />}</div>
    </div>
  );
}
