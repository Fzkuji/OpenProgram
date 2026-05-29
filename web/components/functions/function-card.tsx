"use client";

import { useRef } from "react";

import styles from "./function-card.module.css";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  SquarePenIcon,
} from "@/components/animated-icons";

export interface ProgramSummary {
  name: string;
  category?: string;
  description?: string;
  mtime?: number;
}

export function FunctionCard({
  p,
  icon,
  fav,
  folderName,
  formatDate,
  onClick,
  onContextMenu,
  onDragStart,
  onToggleFav,
  onChangeIcon,
}: {
  p: ProgramSummary;
  icon: string;
  fav: boolean;
  folderName: string | null;
  formatDate: (ts?: number) => string;
  onClick: () => void;
  onContextMenu: (e: React.MouseEvent) => void;
  onDragStart: (e: React.DragEvent) => void;
  onToggleFav: (e: React.MouseEvent) => void;
  onChangeIcon: (e: React.MouseEvent) => void;
}) {
  const { text } = useTranslation();
  const editIconRef = useRef<AnimatedNavIconHandle>(null);
  const desc = p.description ? p.description.split(".")[0] : "";
  return (
    <div
      data-function-card
      className={styles.card}
      draggable
      onDragStart={onDragStart}
      onClick={onClick}
      onContextMenu={onContextMenu}
    >
      <div className={styles.cardIcon}>
        <span className={styles.cardIconEmoji}>{icon}</span>
        <button
          type="button"
          className={styles.cardIconEditBtn}
          onClick={onChangeIcon}
          onMouseEnter={() => editIconRef.current?.startAnimation?.()}
          onMouseLeave={() => editIconRef.current?.stopAnimation?.()}
          title={text("Change icon", "更换图标")}
          aria-label={text("Change icon", "更换图标")}
        >
          <SquarePenIcon ref={editIconRef} size={12} />
        </button>
      </div>
      <div className={styles.cardInfo}>
        <div className={styles.cardName}>{p.name}</div>
        <div className={styles.cardDesc}>{desc}</div>
        <div className={styles.cardMeta}>
          {folderName ? `📁 ${folderName} · ` : ""}
          {formatDate(p.mtime)}
        </div>
      </div>
      <button
        className={fav ? `${styles.favBtn} ${styles.favorited}` : styles.favBtn}
        onClick={onToggleFav}
      >
        {fav ? "★" : "☆"}
      </button>
    </div>
  );
}

export const cardListClass = styles.list;
export const cardGridClass = styles.grid;
