/** One serialized writer per document; navigation does not discard in-flight edits. */
import { errorDetailText } from "../../../lib/net/error-text.ts";

/** The pause before a failed read is tried again: 0.5 s doubling to 30 s (as lib/abilities/kept-read.ts). */
function retryDelay(attempt: number): number {
  return Math.min(500 * 2 ** attempt, 30_000);
}

/** A 4xx other than 408/425/429 is the server's final answer (as isPermanent in lib/abilities/kept-read.ts). */
function permanent(status: number): boolean {
  return status >= 400 && status < 500 && status !== 408 && status !== 425 && status !== 429;
}

/** What a memory file read or save answers. */
interface MemoryFileReply {
  content?: unknown;
  warning?: unknown;
  removed?: unknown;
  error?: unknown;
}

export interface DraftState {
  content: string;
  base: string;
  original: string;
  loaded: boolean;
  saving: boolean;
  restoring: boolean;
  error: string;
  warning: string;
  /** The last save left only headings, so the server keeps no file for it. */
  removed: boolean;
  /** The file is not there (or the server refuses it): its editor closes. */
  gone: boolean;
}
export class MemoryDraft {
  state: DraftState = { content: "", base: "", original: "", loaded: false, saving: false, restoring: false, error: "", warning: "", removed: false, gone: false };
  listeners = new Set<() => void>();
  timer: ReturnType<typeof setTimeout> | undefined;
  loading: Promise<void> | undefined;
  reloadTimer: ReturnType<typeof setTimeout> | undefined;
  loadFailures = 0;
  editVersion = 0;
  undoStack: string[] = [];
  redoStack: string[] = [];
  readonly url: string;
  readonly request: typeof fetch;
  readonly storage?: Storage;
  constructor(url: string, request: typeof fetch = fetch, storage?: Storage) {
    this.url = url; this.request = request.bind(globalThis); this.storage = storage;
  }
  get key() { return "memory-draft:" + this.url; }
  publish(patch: Partial<DraftState>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }
  persist() {
    try {
      if (this.state.content === this.state.base) this.storage?.removeItem(this.key);
      else this.storage?.setItem(this.key, JSON.stringify({ content: this.state.content, base: this.state.base, original: this.state.original }));
    } catch {
      this.publish({ warning: "Local draft storage is unavailable. Keep this page open until saving finishes." });
    }
  }
  async load() {
    if (this.loading) return this.loading;
    if (this.state.loaded && (this.state.saving || this.state.restoring || this.state.content !== this.state.base)) return;
    const editVersion = this.editVersion;
    clearTimeout(this.reloadTimer);
    this.loading = (async () => {
      try {
        const response = await this.request(this.url);
        // A file that is not there (or refused) is not read again: its editor closes.
        if (permanent(response.status)) {
          console.warn(`[memory] ${this.url} is not there: HTTP ${response.status}`);
          this.loadFailures = 0;
          this.publish({ gone: true });
          return;
        }
        if (!response.ok) throw new Error(`Could not load memory: HTTP ${response.status}`);
        const data = await response.json() as MemoryFileReply;
        this.loadFailures = 0;
        if (this.editVersion !== editVersion) return;
        let draft;
        try { draft = JSON.parse(this.storage?.getItem(this.key) || "null"); } catch { /* no recoverable draft */ }
        const valid = draft && typeof draft.content === "string" && typeof draft.base === "string";
        const content = typeof data.content === "string" ? data.content : "";
        this.publish({
          content: valid ? draft.content : content,
          base: valid ? draft.base : content,
          original: valid ? draft.original ?? draft.base : content,
          loaded: true, error: valid && draft.base !== content ? "This memory changed elsewhere. Review the latest version before retrying." : "",
          // A file held aside opens saying what keeps it out of memory.
          warning: typeof data.warning === "string" ? data.warning : "",
          // What the server answers with is a file it keeps.
          removed: false,
          gone: false,
        });
        if (valid && !this.state.error) this.schedule();
      } catch (error) {
        // Nothing on screen: the read is tried again after a growing pause.
        console.warn(`[memory] ${this.url} read failed; trying again`, error);
        this.reloadTimer = setTimeout(() => { void this.load(); }, retryDelay(this.loadFailures++));
      }
    })().finally(() => { this.loading = undefined; });
    return this.loading;
  }
  edit(content: string, record = true) {
    if (this.state.restoring || content === this.state.content) return;
    if (record) {
      this.undoStack.push(this.state.content);
      this.redoStack = [];
    }
    this.editVersion += 1;
    this.publish({ content });
    this.persist();
    if (!this.state.error) this.schedule();
  }
  schedule() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { void this.flush(); }, 0);
  }
  async flush() {
    clearTimeout(this.timer);
    if (!this.state.loaded || this.state.saving || this.state.restoring || this.state.error || this.state.content === this.state.base) return;
    const content = this.state.content;
    const base = this.state.base;
    this.publish({ saving: true });
    try {
      const response = await this.request(this.url, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, base_content: base, autosave: true }),
      });
      const result = await response.json() as MemoryFileReply;
      if (!response.ok) throw new Error(errorDetailText(result.error, "Could not save memory"));
      const saved = typeof result.content === "string" ? result.content : content;
      this.publish({
        base: saved,
        content: this.state.content === content ? saved : this.state.content,
        warning: typeof result.warning === "string" ? result.warning : "",
        removed: result.removed === true,
      });
      this.persist();
    } catch (error) { this.publish({ error: error instanceof Error ? error.message : String(error) }); }
    finally {
      this.publish({ saving: false });
      if (!this.state.error && this.state.content !== this.state.base) this.schedule();
    }
  }
  undo() {
    if (this.state.restoring) return;
    const content = this.undoStack.pop();
    if (content === undefined) return;
    this.redoStack.push(this.state.content);
    this.edit(content, false);
  }
  redo() {
    if (this.state.restoring) return;
    const content = this.redoStack.pop();
    if (content === undefined) return;
    this.undoStack.push(this.state.content);
    this.edit(content, false);
  }
  retry() {
    this.publish({ error: "" });
    if (!this.state.loaded) void this.load();
    else void this.flush();
  }
}
const drafts = new Map<string, MemoryDraft>();
export function memoryDraft(url: string) {
  let draft = drafts.get(url);
  // A file found gone is asked about afresh when opened again (it may be back).
  if (!draft || draft.state.gone) {
    let storage: Storage | undefined;
    try { storage = window.localStorage; } catch { /* server saving still works */ }
    draft = new MemoryDraft(url, fetch, storage);
    drafts.set(url, draft);
  }
  return draft;
}
