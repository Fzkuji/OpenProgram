import { describe, expect, it } from 'vitest';

import { formatTaskResourceMessage } from '../src/commands/taskResource.js';


describe('formatTaskResourceMessage', () => {
  it('preserves the canonical resource DTO returned by the worker', () => {
    const resource = {
      task_id: 't1',
      status: 'running',
      resource_state: 'active',
      reason_code: null,
    };
    const text = formatTaskResourceMessage('task', {
      task: { id: 't1', resource },
    });

    expect(JSON.parse(text)).toEqual({ task: { id: 't1', resource } });
  });
});
