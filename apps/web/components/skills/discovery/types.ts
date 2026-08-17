/**
 * Shared types for the Skills Discovery panel.
 */
import type { CatalogEntry } from "@/lib/state/skills-store";

export type CatalogState = {
  loading: boolean;
  entries: CatalogEntry[] | null;
  error: string | null;
  /** Namespaced names whose local SKILL.md hash drifted from upstream. */
  outdated: Set<string>;
};

export type Source = {
  url: string;
  label: string;
  /** Namespace folder used when installing from this source. */
  slug: string;
  description: string;
  added: boolean;
  origin: "suggested" | "custom";
};

export type SortKey = "default" | "name" | "stars" | "downloads" | "updated";
