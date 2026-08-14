export function filterTasks(tasks, filter, search) {
  const query = search.trim().toLowerCase();
  return tasks.filter((task) => {
    if (filter !== "all" && task.type !== filter) return false;
    if (!query) return true;
    return [task.title, task.prompt, task.command, task.cron]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
}

export function taskCounts(tasks) {
  return {
    all: tasks.length,
    once: tasks.filter((task) => task.type === "once").length,
    recurring: tasks.filter((task) => task.type === "recurring").length,
    monitor: tasks.filter((task) => task.type === "monitor").length,
  };
}

export function numberedTasks(tasks) {
  return tasks.map((task, index) => ({ task, number: index + 1 }));
}

export function shouldShowSuggestions(tasks, filter, search) {
  return tasks.length === 0 && filter === "all" && !search.trim();
}

export function actionAccessibleName(action, title) {
  return `${action} ${title}`;
}
