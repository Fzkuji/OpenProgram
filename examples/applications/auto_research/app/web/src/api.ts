/** One conversation as the host sends it: its last messages and whether a reply runs. */
export interface ChatMessage {
  id: string; role: "user" | "assistant"; text: string; status?: string;
  tools: { tool: string; status: string; input: string }[];
}
export interface ChatFeed { session_id: string; messages: ChatMessage[]; running: boolean }

// The host bridge: start a backend operation, stream its progress, return its result.
declare global {
  interface Window {
    openprogramApp: {
      run(op: string, input: object, key: string): Promise<{ id: string }>;
      status(id: string, after: number): Promise<{ status: string; error?: string; result?: any; events?: { sequence: number; type: string; value: any }[] }>;
      cancel(id: string): Promise<unknown>;
      load(): Promise<{ value: any; version: number }>;
      save(value: any, version: number): Promise<{ value: any; version: number }>;
      on(event: "chat", listener: (value: ChatFeed) => void): () => void;
      on(event: "composer", listener: (value: { height: number }) => void): () => void;
      chat: {
        open(key: string, folder: string): Promise<{ session_id: string; folder: string; exists: boolean }>;
        note(text: string): Promise<unknown>;
        place(rect: { left: number; right: number; bottom: number } | null): Promise<unknown>;
        reveal(): Promise<boolean>;
        close(): Promise<unknown>;
      };
    };
  }
}

const RUNNING = ["queued", "running", "cancelling", "pending", "starting"];
let project = "";
export const setProject = (path: string) => { project = path; };

export async function op<T = any>(name: string, input: object = {}, onEvent?: (e: any) => void, onStart?: (id: string) => void): Promise<T> {
  const body = name === "projects" || name === "browse" || name === "pick_folder" || !project ? input : { ...input, project };
  const run = await window.openprogramApp.run(name, body, `${name}-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  onStart?.(run.id);
  let cursor = 0;
  // Most operations answer within a few tens of milliseconds: ask soon, then less often.
  let wait = 30;
  for (;;) {
    const s = await window.openprogramApp.status(run.id, cursor);
    for (const e of s.events || []) {
      cursor = Math.max(cursor, e.sequence ?? cursor);
      if (e.type === "progress") onEvent?.(e.value);
    }
    if (!RUNNING.includes(s.status)) {
      if (s.error) throw new Error(s.error);
      if (s.status !== "completed") throw new Error(`运行${s.status === "cancelled" ? "已停止" : "未完成：" + s.status}`);
      return s.result as T;
    }
    await new Promise((r) => setTimeout(r, wait));
    wait = Math.min(wait * 1.6, 400);
  }
}

export const cancel = (id: string) => window.openprogramApp.cancel(id);

// App storage: the open project and the recent list.
export interface Saved { project?: string; recent?: { path: string; name: string; branch?: string; head?: string; at: string }[] }
let saved = { value: {} as Saved, version: 0 };
export async function loadSaved(): Promise<Saved> {
  try { saved = await window.openprogramApp.load(); saved.value ||= {}; } catch { saved = { value: {}, version: 0 }; }
  return saved.value;
}
export async function store(update: (v: Saved) => Saved): Promise<Saved> {
  const value = update(saved.value);
  try { saved = await window.openprogramApp.save(value, saved.version); }
  catch { try { saved = await window.openprogramApp.load(); saved = await window.openprogramApp.save(update(saved.value || {}), saved.version); } catch { saved.value = value; } }
  return saved.value;
}
