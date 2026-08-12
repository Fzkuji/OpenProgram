import { formatTaskResource } from '../screens/repl/pickerRouter.js';
import type { TaskRow } from '../ws/client.js';

/**
 * Compact transcript rendering for the explicit /tasks and /task views.
 * Mirrors the Python CLI's `_format_view` (openprogram/_cli_cmds/tasks.py):
 * one header line per task (id, status, resource state) followed by the
 * indented resource counters. Spawn / cancel results are NOT rendered here —
 * they carry their own confirmation output.
 */
export const formatTaskResourceMessage = (
  type: 'tasks_list' | 'task',
  data: unknown,
): string => {
  const d = (data ?? {}) as { tasks?: TaskRow[]; task?: TaskRow | null };
  const tasks = type === 'tasks_list'
    ? (d.tasks ?? [])
    : (d.task ? [d.task] : []);
  if (tasks.length === 0) return 'No tasks.';
  return tasks.map((task) => [
    `${task.id ?? '?'}  ${task.status ?? '?'}  `
      + `resource=${task.resource?.resource_state ?? 'unmetered'}`,
    ...formatTaskResource(task.resource).map((line) => `  ${line}`),
  ].join('\n')).join('\n\n');
};
