import { describe, it, expect } from 'vitest';
import { branchDeletedOutcome } from '../src/screens/repl/wsHandlers/branchDeleted.js';

describe('branchDeletedOutcome', () => {
  it('reports a refusal instead of claiming the branch was deleted', () => {
    const out = branchDeletedOutcome({ ok: false, error: "This is the conversation's only branch" });
    expect(out.text).toContain('not deleted');
    expect(out.text).toContain('only branch');
    expect(out.reload).toBe(false);
  });

  it('reloads the transcript when the selected branch was deleted', () => {
    expect(branchDeletedOutcome({ ok: true, deleted: 4, new_head: 'r1' }))
      .toEqual({ text: 'Deleted the branch (4 messages removed); switched to another branch.', reload: true });
  });

  it('leaves the transcript when another branch was deleted', () => {
    expect(branchDeletedOutcome({ ok: true, deleted: 2, new_head: null }).reload).toBe(false);
  });
});
