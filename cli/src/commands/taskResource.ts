export const formatTaskResourceMessage = (
  _type: 'tasks_list' | 'task' | 'spawn_task_result' | 'cancel_task_result',
  data: unknown,
): string => JSON.stringify(data, null, 2);
