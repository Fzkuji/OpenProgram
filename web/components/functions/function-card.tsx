"use client";

import styles from "./function-card.module.css";

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
          title="Change icon"
          aria-label="Change icon"
        >
          <svg
            viewBox="0 0 24 24"
            width="12"
            height="12"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 20h9" />
            <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
          </svg>
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
