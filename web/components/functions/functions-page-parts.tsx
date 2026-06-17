"use client";

// Small presentational parts split out of functions-page.tsx (was 700+
// lines). cls() class joiner, RenameInput inline editor, ProfileNavRow.

import { cloneElement, isValidElement, useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";

import type { AnimatedNavIconHandle } from "@/components/animated-icons";

import styles from "./functions-page.module.css";


export function RenameInput({
  initial,
  placeholder,
  onCommit,
  onCancel,
}: {
  initial: string;
  placeholder?: string;
  onCommit: (name: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  return (
    <input
      ref={ref}
      className={styles.renameInput}
      type="text"
      placeholder={placeholder}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onCommit(value);
        } else if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
        e.stopPropagation();
      }}
      onBlur={() => onCommit(value)}
    />
  );
}

export function cls(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}

/** One folder-nav row. The animated icon is driven from the whole row's
 *  hover (controlled mode) via a cloned ref, like the sidebar / chats nav. */
export function ProfileNavRow({
  icon,
  name,
  count,
  active,
  dragOver,
  onClick,
  onDragOver,
  onDragLeave,
  onDrop,
  onContextMenu,
}: {
  icon: ReactElement;
  name: string;
  count: number;
  active: boolean;
  dragOver?: boolean;
  onClick: () => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDragLeave?: () => void;
  onDrop?: (e: React.DragEvent) => void;
  onContextMenu?: (e: React.MouseEvent) => void;
}) {
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  return (
    <div
      className={cls(
        styles.folderItem,
        active && styles.active,
        dragOver && styles.dragOver,
      )}
      onClick={onClick}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      onContextMenu={onContextMenu}
      onMouseEnter={() => iconRef.current?.startAnimation?.()}
      onMouseLeave={() => iconRef.current?.stopAnimation?.()}
    >
      <span className={styles.folderIcon}>
        {isValidElement(icon)
          ? cloneElement(icon as ReactElement, { ref: iconRef } as Record<string, unknown>)
          : icon}
      </span>
      <span className={styles.profileName}>{name}</span>
      <span className={styles.folderCount}>{count}</span>
    </div>
  );
}
