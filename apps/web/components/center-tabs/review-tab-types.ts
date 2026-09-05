export type ReviewScope = "turn" | "branch" | "workspace";
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
