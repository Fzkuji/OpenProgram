export type ReviewScope = "turn" | "unstaged" | "staged" | "branch";
export type ReviewCategory = "All" | "Code" | "Tests" | "Docs" | "Large";
export type ReviewSort = "path" | "alpha" | "category" | "recent";

export interface ReviewFile {
  path: string;
  rel: string;
  op: string;
  added: number | null;
  removed: number | null;
  binary?: boolean;
  diff_state?: string;
  turn_ids?: string[];
  producer_turn_id?: string;
  origin_turn_id?: string;
  actor_id?: string;
  actor_ids?: string[];
  job_id?: string | null;
  job_ids?: string[];
}
export interface LinkedImpact {
  job_id?: string;
  relation?: string;
  status?: string;
  origin_turn_id?: string;
  worktree_id?: string | null;
}

export interface ScopeState {
  loading: boolean;
  status: string;
  source?: string;
  files: ReviewFile[];
  file_count: number;
  added: number | null;
  removed: number | null;
  snapshot_id?: string;
  cursor?: string | null;
  next_cursor?: string | null;
  prev_cursor?: string | null;
  page?: number;
  error?: string;
  linked_impacts: LinkedImpact[];
  category?: ReviewCategory;
  query?: string;
  sort?: ReviewSort;
  /** Unstaged and Staged scopes of a local Git project can stage. */
  staging?: boolean;
}

export interface DiffState {
  loading: boolean;
  path?: string;
  diff?: string;
  diff_state?: string;
  cursor?: string | null;
  next_cursor?: string | null;
  prev_cursor?: string | null;
  line_count?: number;
  error?: string;
}

/** A line comment waiting to be sent to the agent. */
export interface ReviewComment {
  id: string;
  scope: ReviewScope;
  path: string;
  rel: string;
  /** "old" for a removed line, numbered in the old file; else "new". */
  side: "old" | "new";
  line: number;
  /** The line's text, without its diff sign. */
  quote: string;
  body: string;
}

export type FindingSeverity = "low" | "medium" | "high";

/** One finding of Review with agent, on a file line of its scope. */
export interface ReviewFinding {
  id: string;
  scope: ReviewScope;
  rel: string;
  line: number;
  severity: FindingSeverity;
  body: string;
}
