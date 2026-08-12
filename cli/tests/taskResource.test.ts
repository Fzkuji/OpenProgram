import { describe, expect, it } from 'vitest';

import { formatTaskResourceMessage } from '../src/commands/taskResource.js';


const resource = {
  task_id: 't1',
  status: 'running',
  resource_state: 'live',
  reason_code: 'budget.idle_exhausted',
  retryable: false,
  capacity: {
    scheduler_capacity: 4,
    session_live: { used: 1, limit: 2 },
    session_queued: { used: 0, limit: 3 },
    session_tasks: { used: 2, limit: 10 },
    queue_position: null,
  },
  budget: {
    scope: 'task_with_shared_ancestors',
    tokens: { actual: 10, reserved: 5, limit: 100 },
    cost_usd: {
      actual: null, reserved: '0.10', limit: '1.00',
      known: false, unknown_events: 1,
    },
    runtime_seconds: { used: 15, limit: 60 },
    idle_seconds: { used: 5, limit: 20 },
    shared_remaining: { tokens: 70, cost_usd: null, cost_unknown_events: 1 },
  },
} as const;

const task = { id: 't1', status: 'running', resource } as never;


describe('formatTaskResourceMessage', () => {
  it('renders a compact header plus indented counters for /task', () => {
    expect(formatTaskResourceMessage('task', { task })).toBe([
      't1  running  resource=live',
      '  Session 1/2 live · 0/3 queued · 2/10 tasks · Scheduler 4',
      '  Tokens: local 85 · shared 70',
      '  Cost: Unknown (1 event)',
      '  Runtime: 45s',
      '  Idle: 15s',
      '  Reason: budget.idle_exhausted',
    ].join('\n'));
  });

  it('separates multiple tasks and never emits raw JSON', () => {
    const text = formatTaskResourceMessage('tasks_list', {
      tasks: [task, { id: 't2', status: 'queued' }],
    });
    expect(text).toContain('t1  running  resource=live');
    expect(text).toContain('t2  queued  resource=unmetered');
    expect(text).toContain('  Unmetered');
    expect(text).not.toContain('{');
  });

  it('reports an empty list', () => {
    expect(formatTaskResourceMessage('tasks_list', { tasks: [] })).toBe('No tasks.');
    expect(formatTaskResourceMessage('task', { task: null })).toBe('No tasks.');
  });
});
