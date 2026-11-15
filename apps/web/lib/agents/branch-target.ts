/** Where a settings request goes: the branch the pane shows, whatever is
 *  selected elsewhere. A conversation with no branch recorded yet guards the
 *  first one the server creates. */
export function branchTarget(branchId: string | null | undefined): Record<string, unknown> {
  return branchId ? { branch_id: branchId } : { expected_branch_id: null };
}
