/** One serialized writer per document; navigation does not discard in-flight edits. */
export interface DraftState {
  content: string;
  base: string;
  original: string;
  loaded: boolean;
  saving: boolean;
  error: string;
  warning: string;
}
export class MemoryDraft {
  state: DraftState = { content: "", base: "", original: "", loaded: false, saving: false, error: "", warning: "" };
  listeners = new Set<() => void>();
  timer: ReturnType<typeof setTimeout> | undefined;
  loading: Promise<void> | undefined;
  editVersion = 0;
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
    if (this.state.loaded && (this.state.saving || this.state.content !== this.state.base)) return;
    const editVersion = this.editVersion;
    this.loading = (async () => {
      try {
        const response = await this.request(this.url);
        if (!response.ok) throw new Error("Could not load memory");
        const data = await response.json();
        if (this.editVersion !== editVersion) return;
        let draft;
        try { draft = JSON.parse(this.storage?.getItem(this.key) || "null"); } catch { /* no recoverable draft */ }
        const valid = draft && typeof draft.content === "string" && typeof draft.base === "string";
        this.publish({
          content: valid ? draft.content : data.content,
          base: valid ? draft.base : data.content,
          original: valid ? draft.original ?? draft.base : data.content,
          loaded: true, error: valid && draft.base !== data.content ? "This memory changed elsewhere. Review the latest version before retrying." : "",
        });
        if (valid && !this.state.error) this.schedule();
      } catch (error) { this.publish({ error: String(error) }); }
    })().finally(() => { this.loading = undefined; });
    return this.loading;
  }
  edit(content: string) {
    this.editVersion += 1;
    this.publish({ content });
    this.persist();
    if (!this.state.error) this.schedule();
  }
  schedule() {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { void this.flush(); }, 800);
  }
  async flush() {
    clearTimeout(this.timer);
    if (!this.state.loaded || this.state.saving || this.state.error || this.state.content === this.state.base) return;
    const content = this.state.content;
    const base = this.state.base;
    this.publish({ saving: true });
    try {
      const response = await this.request(this.url, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, base_content: base }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "Could not save memory");
      this.publish({
        base: result.content,
        content: this.state.content === content ? result.content : this.state.content,
        warning: result.warning || "",
      });
      this.persist();
    } catch (error) { this.publish({ error: error instanceof Error ? error.message : String(error) }); }
    finally {
      this.publish({ saving: false });
      if (!this.state.error && this.state.content !== this.state.base) this.schedule();
    }
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
  if (!draft) {
    let storage: Storage | undefined;
    try { storage = window.localStorage; } catch { /* server saving still works */ }
    draft = new MemoryDraft(url, fetch, storage);
    drafts.set(url, draft);
  }
  return draft;
}
