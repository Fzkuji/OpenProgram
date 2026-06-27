/** Shape of the per-commit metadata + per-item record + detail payload
 *  the backend (ws_actions/context_commits.py) sends. Kept in one file
 *  so every subcomponent imports the same source of truth. */

export type StateName = "full" | "aged" | "cleared" | "summarized" | "summary";

export interface CommitMeta {
  id: string;
  commit_parent: string | null;
  created_at: number;
  head_node_id: string;
  /** Commits sharing the same turn_group_id are parallel attempts
   *  (same DAG fork point: multi-agent / modify-retry siblings) and
   *  collapse into one row with an attempt switcher. */
  turn_group_id: string;
  total_tokens: number;
  rules_version?: string;
  summary: string;
  item_count: number;
  state_counts: Partial<Record<StateName, number>>;
}

export interface CommitItem {
  source_node_id: string;
  role: string;
  state: StateName;
  rendered: string;
  tokens: number;
  reason: string;
  locked: boolean;
  is_anchor: boolean;
  merged_into: string | null;
}

export interface CommitDetail {
  id: string;
  session_id?: string;
  commit_parent?: string | null;
  created_at?: number;
  head_node_id?: string;
  total_tokens?: number;
  summary?: string;
  items: CommitItem[];
  error?: string | null;
}

/** Background tint + foreground colour per item state. "full" is
 *  rendered muted so non-trivial states (aged / cleared / summary)
 *  visually pop without being garish. */
export const STATE_COLOR: Record<StateName, { bg: string; fg: string }> = {
  full:       { bg: "rgba(255, 255, 255, 0.06)", fg: "var(--text-muted)" },
  aged:       { bg: "rgba(227, 179, 65, 0.14)",  fg: "#e3b341" },
  cleared:    { bg: "rgba(248, 81, 73, 0.14)",   fg: "#f85149" },
  summarized: { bg: "rgba(110, 118, 129, 0.20)", fg: "var(--text-muted)" },
  summary:    { bg: "rgba(86, 211, 100, 0.14)",  fg: "#56d364" },
};
