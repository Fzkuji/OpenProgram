/**
 * Which git pill owns a repository.
 *
 * Two working folders can sit in the same Git checkout (the project root
 * and one of its subfolders, say). Their branch, changes and PR are the
 * same, so only one pill should show. Each mounted pill claims its
 * checkout root with an order (the project folder first, then working
 * folders in list order); the lowest order wins and the rest hide.
 * Separate worktrees have separate roots, so each keeps its own pill.
 */
import { useEffect, useSyncExternalStore } from "react";

type Claim = { root: string; order: number };

const claims = new Map<string, Claim>();
const listeners = new Set<() => void>();
let version = 0;

function emit() {
  version += 1;
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function owns(id: string): boolean {
  const mine = claims.get(id);
  if (!mine) return true;
  for (const [otherId, other] of claims) {
    if (otherId === id || other.root !== mine.root) continue;
    if (other.order < mine.order || (other.order === mine.order && otherId < id)) return false;
  }
  return true;
}

/** True while this pill is the visible one for its checkout root. */
export function useGitRepoClaim(id: string, root: string | null, order: number): boolean {
  useEffect(() => {
    if (!root) return;
    claims.set(id, { root, order });
    emit();
    return () => {
      claims.delete(id);
      emit();
    };
  }, [id, root, order]);
  return useSyncExternalStore(
    subscribe,
    () => `${version}:${owns(id)}`,
    () => "0:true",
  ).endsWith("true");
}

/** Tell every pill on a checkout that its git state changed. */
export function notifyGitChanged(root?: string | null) {
  window.dispatchEvent(new CustomEvent("op:git-changed", { detail: { root: root ?? null } }));
}
