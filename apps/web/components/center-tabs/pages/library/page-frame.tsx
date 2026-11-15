"use client";

/** The shared frame of the built-in library pages: title, search and body. */
import { SearchInput } from "@/components/ui/search-input";
import styles from "../pages.module.css";

/** Shared page chrome: title row + search box, then the caller's list. */
export function PageFrame({
  icon,
  title,
  query,
  onQueryChange,
  searchPlaceholder,
  action,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  query: string;
  onQueryChange: (value: string) => void;
  searchPlaceholder: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className={styles.builtinPane}>
      <div className={styles.builtinInner}>
        <div className={styles.builtinHeader}>
          {icon}
          <h1 className={styles.builtinTitle}>{title}</h1>
          {action}
        </div>
        <SearchInput
          className="mx-1 mb-2.5"
          value={query}
          onChange={onQueryChange}
          placeholder={searchPlaceholder}
          aria-label={searchPlaceholder}
        />
        {children}
      </div>
    </div>
  );
}
