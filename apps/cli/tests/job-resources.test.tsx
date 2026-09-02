import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import { handleSlash, type SlashContext } from '../src/commands/handler.js';
import { buildPickerNode, type PickerCtx } from '../src/screens/repl/pickerRouter.js';
import type { WsEnvelope, WsRequest } from '../src/ws/client.js';


const resource = {
  job_id: 'job-1',
  status: 'running',
  resource_state: 'live',
  reason_code: 'budget.idle_exhausted',
  reason_key: 'resource.reason.budget.idle_exhausted',
  retryable: false,
  event_cursor: {
    execution_id: 'job-1',
    next_sequence: 3,
    snapshot_status_version: 2,
  },
  limits: { scheduler_capacity: 4, limits: {} },
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
      actual: null,
      reserved: '0.10',
      limit: '1.00',
      known: false,
      unknown_events: 1,
    },
    runtime_seconds: { used: 15, limit: 60 },
    idle_seconds: { used: 5, limit: 20 },
    shared_remaining: {
      tokens: 70,
      cost_usd: null,
      cost_unknown_events: 1,
    },
  },
} as const;

const job = {
  id: 'job-1',
  execution_id: 'job-1',
  project_id: 'default',
  session_id: 'session-1',
  parent_execution_id: null,
  subject: 'Research limits',
  status: 'running',
  status_version: 2,
  parent_session_id: 'session-1',
  capabilities: {
    pause: true, step: true, steer: false, fork: false, retry: false,
    safe_point_kinds: [], state_schema_version: 1,
  },
  checkpoint_head_id: null,
  event_cursor: {
    execution_id: 'job-1', next_sequence: 3, snapshot_status_version: 2,
  },
  resource,
};

type Setter<T> = (value: T | ((previous: T) => T)) => void;
const apply = <T,>(previous: T, value: T | ((previous: T) => T)): T =>
  typeof value === 'function'
    ? (value as (previous: T) => T)(previous)
    : value;


describe('job resource WebSocket contract', () => {
  it('uses canonical execution commands and real response envelopes', async () => {
    const requests: WsRequest[] = [
      {
        type: 'execution.command', action: 'execution.pause',
        command_id: 'cmd-pause', execution_id: 'job-1', expected_version: 2,
        payload: {},
      },
      {
        type: 'execution.command', action: 'execution.continue',
        command_id: 'cmd-continue', execution_id: 'job-1', expected_version: 2,
        payload: {},
      },
      {
        type: 'execution.command', action: 'execution.step',
        command_id: 'cmd-step', execution_id: 'job-1', expected_version: 2,
        payload: {},
      },
      {
        type: 'execution.command', action: 'execution.cancel',
        command_id: 'cmd-cancel', execution_id: 'job-1', expected_version: 2,
        payload: { reason_code: 'cancel.user' },
      },
    ];
    expect(requests.map((request) => request.action)).toEqual([
      'execution.pause', 'execution.continue', 'execution.step',
      'execution.cancel',
    ]);

    const module = await import('../src/screens/repl/useWsEvents.js');
    const handleJobEnvelope = (module as Record<string, unknown>).handleJobEnvelope;
    expect(handleJobEnvelope).toBeTypeOf('function');
    if (typeof handleJobEnvelope !== 'function') return;

    let jobs: unknown[] = [];
    let selected: unknown = null;
    let picker: string | null = 'jobs';
    const systemLines: string[] = [];
    const ctx = {
      setJobsList: ((next) => { jobs = apply(jobs, next); }) as Setter<unknown[]>,
      setSelectedJob: ((next) => { selected = apply(selected, next); }) as Setter<unknown>,
      setPickerKind: ((next) => { picker = apply(picker, next); }) as Setter<string | null>,
      pushSystem: (text: string) => { systemLines.push(text); },
    };
    const frames: WsEnvelope[] = [
      { type: 'jobs_list', data: { session_id: 'session-1', jobs: [job] } },
      { type: 'job', data: { job } },
    ];

    for (const frame of frames) handleJobEnvelope(frame, ctx);
    expect(selected).toMatchObject({ resource });

    const canonicalResource = {
      admission_id: 'job-1',
      resource_state: 'active',
      queue_wait: null,
      resource_lease_generation: 1,
      owner_instance_id: 'worker-1',
      limits: {},
      usage: {},
      reservation: null,
    };
    const cursor = {
      execution_id: 'job-1',
      next_sequence: 4,
      snapshot_status_version: 3,
    };
    const terminalFrames: WsEnvelope[] = [
      {
        type: 'execution.command.updated',
        data: {
          command: {
            command_id: 'cmd-cancel', execution_id: 'job-1', status: 'applied',
          },
          execution: {
            execution_id: 'job-1', status: 'cancelled', status_version: 3,
            resource: canonicalResource,
          },
          event_cursor: cursor,
        },
      },
    ];

    for (const frame of terminalFrames) handleJobEnvelope(frame, ctx);

    expect(jobs).toHaveLength(1);
    expect(jobs[0]).toMatchObject({
      execution_id: 'job-1',
      status: 'cancelled',
      status_version: 3,
      event_cursor: cursor,
      resource: canonicalResource,
    });
    expect(selected).toMatchObject({ id: 'job-1', status: 'cancelled' });
    expect(picker).toBe('job_detail');
    // Only the explicit list / detail views print; command updates stay
    // silent while the explicit views include the canonical cursor.
    expect(systemLines).toHaveLength(2);
    expect(systemLines.join('\n')).toContain(
      'event_cursor={"execution_id":"job-1","next_sequence":3,"snapshot_status_version":2}',
    );
  });
});


describe('job resource picker and formatter', () => {
  it('formats capacity, local/shared remaining, unknown cost and reason', async () => {
    const module = await import('../src/screens/repl/pickerRouter.js');
    const formatJobResource = (module as Record<string, unknown>).formatJobResource;
    expect(formatJobResource).toBeTypeOf('function');
    if (typeof formatJobResource !== 'function') return;

    expect(formatJobResource(resource)).toEqual([
      'Session 1/2 live · 0/3 queued · 2/10 jobs · Scheduler 4',
      'Tokens: local 85 · shared 70',
      'Cost: Unknown (1 event)',
      'Runtime: 45s',
      'Idle: 15s',
      'Reason: budget.idle_exhausted',
    ]);
    expect(formatJobResource(undefined)).toEqual(['Unmetered']);

    const known = {
      ...resource,
      budget: {
        ...resource.budget,
        cost_usd: {
          ...resource.budget.cost_usd,
          actual: '0.25',
          known: true,
          unknown_events: 0,
        },
        shared_remaining: {
          ...resource.budget.shared_remaining,
          cost_usd: '0.50',
          cost_unknown_events: 0,
        },
      },
    };
    expect(formatJobResource(known)[2]).toBe('Cost: local $0.65 · shared $0.50');
  });

  it('reuses Picker for list, detail and stop', () => {
    const send = vi.fn();
    const base = {
      client: { send } as never,
      pushSystem: vi.fn(),
      conversationId: 'session-1',
      jobsList: [job],
      selectedJob: null,
      setPickerKind: vi.fn(),
      setSelectedJob: vi.fn(),
    } as unknown as PickerCtx;

    const list = buildPickerNode({ ...base, pickerKind: 'jobs' } as PickerCtx)!;
    expect(list.type.name).toBe('Picker');
    expect(list.props.items[0].description).toContain('Session 1/2 live');
    list.props.onSelect(list.props.items[0]);
    expect(send).toHaveBeenCalledWith({ action: 'get_job', job_id: 'job-1' });

    const detail = buildPickerNode({
      ...base,
      pickerKind: 'job_detail',
      selectedJob: job,
    } as PickerCtx)!;
    const labels = detail.props.items.map((item: { label: string }) => item.label);
    expect(labels).toEqual(expect.arrayContaining([
      'Tokens', 'Cost', 'Runtime', 'Idle', 'Reason', 'Stop job',
    ]));
    detail.props.onSelect(detail.props.items.find(
      (item: { value: string }) => item.value === 'stop',
    ));
    expect(send).toHaveBeenCalledWith(expect.objectContaining({
      type: 'execution.command',
      action: 'execution.cancel',
      execution_id: 'job-1',
      expected_version: 2,
      payload: { reason_code: 'cancel.user' },
    }));
  });

  it('/jobs requests the current session and opens the list picker', () => {
    const send = vi.fn();
    const openPicker = vi.fn();
    const ctx = {
      client: { send },
      currentConversation: 'session-1',
      openPicker,
      pushSystem: vi.fn(),
    } as unknown as SlashContext;

    expect(handleSlash('/jobs', ctx)).toBe(true);
    expect(send).toHaveBeenCalledWith({
      action: 'list_jobs', session_id: 'session-1',
    });
    expect(openPicker).toHaveBeenCalledWith('jobs');
  });
});


describe('session resource settings', () => {
  it('/config asks for settings in the current session context', () => {
    const send = vi.fn();
    const ctx = {
      client: { send },
      currentConversation: 'session-1',
      openPicker: vi.fn(),
    } as unknown as SlashContext;

    handleSlash('/config', ctx);

    expect(send).toHaveBeenCalledWith({
      action: 'get_settings', session_id: 'session-1',
    });
  });

  it('keeps configured value and appends session effective/source', async () => {
    const module = await import('../src/components/SettingsPanel.js');
    const suffix = (module as Record<string, unknown>).resourceSettingSuffix;
    expect(suffix).toBeTypeOf('function');
    if (typeof suffix !== 'function') return;

    const row = {
      key: 'agent.resource_limits.max_total_tokens',
      group: 'Agent resources',
      label: 'Max total tokens',
      widget: 'number',
      apply: 'live',
      value: 100,
      effective: 50,
      source: 'session',
    };
    expect(row.value).toBe(100);
    expect(suffix(row)).toBe('effective 50 · session');
  });
});
