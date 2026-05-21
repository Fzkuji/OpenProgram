"use client";

import styles from "./program-card.module.css";

export interface ProgramSummary {
  name: string;
  category?: string;
  description?: string;
  mtime?: number;
}

export function ProgramCard({
  p,
  icon,
  fav,
  folderName,
  formatDate,
  onClick,
  onContextMenu,
  onDragStart,
  onToggleFav,
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
}) {
  const desc = p.description ? p.description.split(".")[0] : "";
  return (
    <div
      data-program-card
      className={styles.card}
      draggable
      onDragStart={onDragStart}
      onClick={onClick}
      onContextMenu={onContextMenu}
    >
      <div className={styles.cardIcon}>{icon}</div>
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
