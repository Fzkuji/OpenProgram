import { describe, expect, it } from 'vitest';

import { formatJobResourceMessage } from '../src/commands/jobResource.js';


const resource = {
  job_id: 't1',
  status: 'running',
  resource_state: 'live',
  reason_code: 'budget.idle_exhausted',
  retryable: false,
  capacity: {
    scheduler_capacity: 4,
    session_live: { used: 1, limit: 2 },
    session_queued: { used: 0, limit: 3 },
    session_jobs: { used: 2, limit: 10 },
    queue_position: null,
  },
  budget: {
    scope: 'job_with_shared_ancestors',
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

const job = { id: 't1', status: 'running', resource } as never;


describe('formatJobResourceMessage', () => {
  it('renders a compact header plus indented counters for /job', () => {
    expect(formatJobResourceMessage('job', { job })).toBe([
      't1  running  resource=live',
      '  Session 1/2 live · 0/3 queued · 2/10 jobs · Scheduler 4',
      '  Tokens: local 85 · shared 70',
      '  Cost: Unknown (1 event)',
      '  Runtime: 45s',
      '  Idle: 15s',
      '  Reason: budget.idle_exhausted',
    ].join('\n'));
  });

  it('separates multiple jobs and never emits raw JSON', () => {
    const text = formatJobResourceMessage('jobs_list', {
      jobs: [job, { id: 't2', status: 'queued' }],
    });
    expect(text).toContain('t1  running  resource=live');
    expect(text).toContain('t2  queued  resource=unmetered');
    expect(text).toContain('  Unmetered');
    expect(text).not.toContain('{');
  });

  it('reports an empty list', () => {
    expect(formatJobResourceMessage('jobs_list', { jobs: [] })).toBe('No jobs.');
    expect(formatJobResourceMessage('job', { job: null })).toBe('No jobs.');
  });
});
