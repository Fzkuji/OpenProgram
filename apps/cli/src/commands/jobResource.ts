import { formatJobResource } from '../screens/repl/pickerRouter.js';
import type { JobRow } from '../ws/client.js';

/**
 * Compact transcript rendering for the explicit /jobs and /job views.
 * Mirrors the Python CLI's `_format_view` (openprogram/cli/commands/jobs.py):
 * one header line per job (id, status, resource state) followed by the
 * indented resource counters. Spawn / cancel results are NOT rendered here —
 * they carry their own confirmation output.
 */
export const formatJobResourceMessage = (
  type: 'jobs_list' | 'job',
  data: unknown,
): string => {
  const d = (data ?? {}) as { jobs?: JobRow[]; job?: JobRow | null };
  const jobs = type === 'jobs_list'
    ? (d.jobs ?? [])
    : (d.job ? [d.job] : []);
  if (jobs.length === 0) return 'No jobs.';
  return jobs.map((job) => [
    `${job.id ?? '?'}  ${job.status ?? '?'}  `
      + `resource=${job.resource?.resource_state ?? 'unmetered'}`,
    ...formatJobResource(job.resource).map((line) => `  ${line}`),
  ].join('\n')).join('\n\n');
};
