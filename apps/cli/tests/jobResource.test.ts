import { describe, expect, it } from 'vitest';

import { formatJobResourceMessage } from '../src/commands/jobResource.js';


const resource = {
  job_id: 't1',
  execution_id: 't1',
  status: 'running',
  event_cursor: {
    execution_id: 't1',
    next_sequence: 3,
    snapshot_status_version: 2,
  },
  execution: { reason_code: 'budget.idle_exhausted' },
  resource: {
    resource_state: 'live',
    limits: { scheduler_capacity: 4 },
    usage: {
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
  },
} as const;

const job = {
  id: 't1',
  execution_id: 't1',
  status: 'running',
  resource,
} as const;


describe('formatJobResourceMessage', () => {
  it('renders a compact header plus indented counters for /job', () => {
    expect(formatJobResourceMessage('job', { job })).toBe([
      't1  running  resource=live',
      '  event_cursor={"execution_id":"t1","next_sequence":3,"snapshot_status_version":2}',
      '  State: live',
      '  Tokens: local 85 · shared 70',
      '  Cost: Unknown (1 event)',
      '  Runtime: 45s',
      '  Idle: 15s',
      '  Reason: budget.idle_exhausted',
    ].join('\n'));
  });

  it('separates multiple jobs and includes the canonical cursor', () => {
    const text = formatJobResourceMessage('jobs_list', {
      jobs: [job, { id: 't2', status: 'queued' }],
    });
    expect(text).toContain('t1  running  resource=live');
    expect(text).toContain(
      'event_cursor={"execution_id":"t1","next_sequence":3,"snapshot_status_version":2}',
    );
    expect(text).toContain('t2  queued  resource=unmetered');
    expect(text).toContain('  Unmetered');
  });

  it('reports an empty list', () => {
    expect(formatJobResourceMessage('jobs_list', { jobs: [] })).toBe('No jobs.');
    expect(formatJobResourceMessage('job', { job: null })).toBe('No jobs.');
  });
});
