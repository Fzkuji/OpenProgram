"use client";

import { useRef } from "react";

import styles from "./function-card.module.css";
import { useTranslation } from "@/lib/i18n";
import {
  type AnimatedNavIconHandle,
  FoldersIcon,
  SquarePenIcon,
  WrenchIcon,
} from "@/components/animated-icons";
import { DEFAULT_ICON, FUNCTION_ICONS, normalizeIcon } from "./icon-picker";

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
  const cardIconRef = useRef<AnimatedNavIconHandle>(null);
  const desc = p.description ? p.description.split(".")[0] : "";
  const Icon = FUNCTION_ICONS[normalizeIcon(icon)] ?? FUNCTION_ICONS[DEFAULT_ICON];
  return (
    <div
      data-function-card
      className={styles.card}
      draggable
      onDragStart={onDragStart}
      onClick={onClick}
      onContextMenu={onContextMenu}
      onMouseEnter={() => cardIconRef.current?.startAnimation?.()}
      onMouseLeave={() => cardIconRef.current?.stopAnimation?.()}
    >
      <div className={styles.cardIcon}>
        <Icon ref={cardIconRef} size={18} />
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
          {folderName ? (
            <>
              <FoldersIcon size={11} className={styles.cardMetaIcon} aria-hidden="true" />
              {` ${folderName} · `}
            </>
          ) : null}
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

/** A built-in (regular) tool — same card anatomy and styling as
 *  FunctionCard, minus every interactive affordance: tools are fixed
 *  framework plumbing (no run, no favourite, no icon edit, no drag). */
export function ToolCard({ name, description }: { name: string; description: string }) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <div
      className={styles.card}
      style={{ cursor: "default" }}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
    >
      <div className={styles.cardIcon}>
        <WrenchIcon ref={iconRef} size={18} />
      </div>
      <div className={styles.cardInfo}>
        <div className={styles.cardName}>{name}</div>
        <div className={styles.cardDesc}>{description}</div>
      </div>
    </div>
  );
}

export const cardListClass = styles.list;
export const cardGridClass = styles.grid;
