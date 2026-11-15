/**
 * Whether a turn's frame belongs to a branch other than the one on screen.
 *
 * Every branch of a conversation can run at once, and switching branches
 * never waits for a turn. A turn's frames name its branch (`branch_id`);
 * the page draws, and keeps run state for, only the selected branch's
 * turn. A frame without a branch, or a page that does not know its
 * selection yet, is the selected branch's.
 */
export function offSelectedBranch(frameBranch: unknown, selected: string | null | undefined): boolean {
  return typeof frameBranch === "string" && !!frameBranch
    && typeof selected === "string" && !!selected
    && frameBranch !== selected;
}
