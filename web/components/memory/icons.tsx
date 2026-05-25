"use client";

/**
 * Small icons + the colored TypeBadge used in the Memory page
 * tree row and preview header.
 */

import styles from "./memory-page.module.css";

export const TYPE_COLORS: Record<string, string> = {
  concept: "#7c6fcd",
  entity: "#3b82f6",
  event: "#f59e0b",
  relation: "#10b981",
  procedure: "#06b6d4",
  user: "#ec4899",
  source: "#f97316",
  query: "#84cc16",
  synthesis: "#a855f7",
  attribute: "#6b7280",
  meta: "#ef4444",
  index: "#8b5cf6",
};

export function TypeBadge({ type }: { type: string }) {
  if (!type) return null;
  const color = TYPE_COLORS[type.toLowerCase()] ?? "#6b7280";
  return (
    <span className={styles.typeBadge} style={{ background: color + "22", color, borderColor: color + "44" }}>
      {type}
    </span>
  );
}

export function DocIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none">
      <rect x="2" y="1" width="10" height="13" rx="1.5" stroke="currentColor" strokeWidth="1.2"/>
      <path d="M4.5 5h5M4.5 7.5h5M4.5 10h3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  );
}

export function ClockIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.2"/>
      <path d="M8 5v3l2 1.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
    </svg>
  );
}

