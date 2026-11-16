import { wsRequest } from "@/lib/net/ws/ws-request";

/**
 * The project list as one conversation sees it (`current_project_id` is
 * that conversation's project; the empty key is the sidebar's list).
 *
 * Many parts of the page ask for it: the sidebar, the top bar's project
 * chip, the working-folder chips, the file tree, and every reply that links
 * the files it names, including replies that mount after the answer came
 * back. Readers share one answer per key, the one on its way or the one
 * already settled, so opening a conversation costs one request.
 *
 * Every change to a project or to a conversation's project is broadcast as
 * `projects_changed`, which fires `project-changed`: that drops every kept
 * answer, so a reader asking after it never gets a list from before the
 * change. A reconnect drops them too (a broadcast may have been missed),
 * and a kept answer expires after ANSWER_TTL_MS as a backstop.
 */
export const ANSWER_TTL_MS = 30_000;

interface Entry {
  request: Promise<unknown>;
  settledAt: number | null;
}

const answers = new Map<string, Entry>();

export function forgetProjectLists(): void {
  answers.clear();
}

if (typeof window !== "undefined") {
  // Registered when the module loads, so it runs before any reader's own
  // `project-changed` listener asks again.
  window.addEventListener("project-changed", forgetProjectLists);
  window.addEventListener("op:browser-connection", forgetProjectLists);
}

export function listSessionProjects<T extends { session_id?: string | null }>(
  sessionId: string | null | undefined,
  now: () => number = () => Date.now(),
): Promise<T | null> {
  const key = sessionId || "";
  const kept = answers.get(key);
  if (kept && (kept.settledAt === null || now() - kept.settledAt < ANSWER_TTL_MS)) {
    return kept.request as Promise<T | null>;
  }
  const request = wsRequest<T>(
    "list_projects",
    { session_id: key },
    "projects_list",
    // Lists asked for other conversations (or none: the sidebar's) are in
    // flight too, and their current project is not this one's. The server
    // echoes the request's session id (null for none).
    (data) => (data.session_id ?? null) === (key || null),
  );
  const entry: Entry = { request, settledAt: null };
  answers.set(key, entry);
  request.then(
    (data) => {
      if (answers.get(key) !== entry) return;
      // No answer (socket closed, timeout, error) is not kept: the next
      // reader asks again.
      const failed = !data || (data as { status?: string }).status === "error";
      if (failed) answers.delete(key);
      else entry.settledAt = now();
    },
    () => {
      if (answers.get(key) === entry) answers.delete(key);
    },
  );
  return request;
}
