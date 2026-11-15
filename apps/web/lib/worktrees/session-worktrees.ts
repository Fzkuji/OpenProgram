"use client";

/**
 * A conversation's agent worktrees, read from the worker
 * (`session_worktrees`) and re-read whenever it broadcasts a
 * `worktree_status` for that conversation (create, merge, discard, keep).
 * The input box's worktree chip, the transcript marker and Activity all
 * read this one copy.
 */
import { create } from "zustand";
import { useEffect } from "react";

import { wsRequest } from "@/lib/net/ws/ws-request";
import type { WorktreeRow } from "./worktree-model";

interface SessionWorktrees {
  sourceRepo: string | null;
  rows: WorktreeRow[];
  loaded: boolean;
}

interface WorktreesState {
  bySession: Record<string, SessionWorktrees>;
  put(sessionId: string, value: SessionWorktrees): void;
}

const EMPTY: SessionWorktrees = { sourceRepo: null, rows: [], loaded: false };

export const useWorktreesStore = create<WorktreesState>((set) => ({
  bySession: {},
  put: (sessionId, value) => set((state) => ({ bySession: { ...state.bySession, [sessionId]: value } })),
}));

const inflight = new Map<string, Promise<void>>();

export function refreshSessionWorktrees(sessionId: string): Promise<void> {
  const running = inflight.get(sessionId);
  if (running) return running;
  const next = wsRequest<{ session_id: string; source_repo: string | null; worktrees: WorktreeRow[] }>(
    "session_worktrees", { session_id: sessionId }, "session_worktrees",
    (data) => data.session_id === sessionId, 15000,
  ).then((data) => {
    if (data) useWorktreesStore.getState().put(sessionId, { sourceRepo: data.source_repo ?? null, rows: data.worktrees ?? [], loaded: true });
  }).finally(() => { inflight.delete(sessionId); });
  inflight.set(sessionId, next);
  return next;
}

let listening = false;
function listen(): void {
  if (listening || typeof window === "undefined") return;
  listening = true;
  window.addEventListener("op:ws-message", (event) => {
    const detail = (event as CustomEvent<{ type?: string; data?: { parent_session?: string | null } }>).detail;
    if (detail?.type !== "worktree_status") return;
    const sessionId = detail.data?.parent_session;
    if (sessionId && useWorktreesStore.getState().bySession[sessionId]) void refreshSessionWorktrees(sessionId);
  });
}

/** The conversation's worktrees; read on first use and kept current. */
export function useSessionWorktrees(sessionId: string | null): SessionWorktrees {
  const value = useWorktreesStore((s) => (sessionId ? s.bySession[sessionId] : undefined));
  useEffect(() => {
    if (!sessionId) return;
    listen();
    void refreshSessionWorktrees(sessionId);
  }, [sessionId]);
  return value ?? EMPTY;
}

type Reply = { worktree_id?: string | null; status?: string | null; error?: string | null };

async function act(sessionId: string, action: string, payload: Record<string, unknown>, responseType: string, worktreeId: string): Promise<string | null> {
  const reply = await wsRequest<Reply>(action, payload, responseType, (data) => data.worktree_id === worktreeId, 120000);
  void refreshSessionWorktrees(sessionId);
  if (!reply) return "No reply from the worker.";
  return reply.error ?? null;
}

/** Give a branch its own worktree; its next turn runs there. Returns an error or null. */
export async function createBranchWorktree(sessionId: string, branchId: string | null): Promise<string | null> {
  const reply = await wsRequest<{ session_id: string; error?: string | null }>(
    "create_branch_worktree", { session_id: sessionId, branch_id: branchId }, "create_branch_worktree_result",
    (data) => data.session_id === sessionId, 120000,
  );
  void refreshSessionWorktrees(sessionId);
  if (!reply) return "No reply from the worker.";
  return reply.error ?? null;
}

/** Merge what the worktree holds, committed or not: fast-forward when possible, else a merge commit. */
export function mergeWorktree(sessionId: string, worktreeId: string, squash = false): Promise<string | null> {
  return act(sessionId, "merge_worktree", { worktree_id: worktreeId, strategy: squash ? "squash" : "auto", commit_pending: true },
    "merge_worktree_result", worktreeId);
}

/** Delete the worktree and its branch, uncommitted work included. Callers confirm first. */
export function discardWorktree(sessionId: string, worktreeId: string): Promise<string | null> {
  return act(sessionId, "discard_worktree", { worktree_id: worktreeId, force: true, delete_branch: true },
    "discard_worktree_result", worktreeId);
}

/** Stop running in the worktree but leave its folder and branch for the person. */
export function keepWorktree(sessionId: string, worktreeId: string): Promise<string | null> {
  return act(sessionId, "keep_worktree", { worktree_id: worktreeId }, "keep_worktree_result", worktreeId);
}
