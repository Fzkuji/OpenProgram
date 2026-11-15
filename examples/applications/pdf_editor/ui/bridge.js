// Thin helpers over window.openprogramApp (injected by the host).
const TERMINAL = new Set(["completed", "failed", "cancelled", "interrupted"]);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

export const api = window.openprogramApp;

// A short stable hash, so identical requests reuse the stored run result.
export function stableKey(prefix, value) {
  const text = JSON.stringify(value);
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761);
    h2 = Math.imul(h2 ^ c, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return `${prefix}:${(h2 >>> 0).toString(16).padStart(8, "0")}${(h1 >>> 0).toString(16).padStart(8, "0")}`;
}

// Runs an operation and waits for its end. Progress events go to onProgress.
export async function op(operation, input = {}, { key, onProgress, onRun } = {}) {
  if (!api) throw new Error("This page must be opened inside OpenProgram");
  let run = await api.run(operation, input, key || crypto.randomUUID());
  onRun?.(run);
  let cursor = 0;
  let delay = 100;
  for (;;) {
    for (const event of run.events || []) {
      cursor = Math.max(cursor, event.sequence);
      if (event.type === "progress") onProgress?.(event.value);
    }
    if (TERMINAL.has(run.status)) break;
    await sleep(delay);
    delay = Math.min(delay * 1.4, 700);
    run = await api.status(run.id, cursor);
  }
  if (run.status !== "completed") {
    const error = new Error(cleanError(run.error) || `The operation was ${run.status}`);
    error.status = run.status;
    throw error;
  }
  return run.result;
}

// Cached operation: the same input reuses the stored result; a failed
// cached run is retried once under a fresh key.
export async function cachedOp(operation, input, options = {}) {
  const key = stableKey(operation, input);
  try {
    return await op(operation, input, { ...options, key });
  } catch (error) {
    if (error.status !== "failed") throw error;
    return op(operation, input, { ...options, key: key + ":" + Date.now().toString(36) });
  }
}

export function cleanError(message) {
  if (!message) return "";
  return String(message).replace(/^(ValueError|FileNotFoundError|PermissionError|RuntimeError|KeyError|Exception): /, "");
}

// Versioned app storage with a merge hook for concurrent writers.
export class Store {
  constructor(defaults) {
    this.value = structuredClone(defaults);
    this.defaults = defaults;
    this.version = null;
    this.chain = Promise.resolve();
    this.listeners = new Set();
  }
  async load() {
    if (!api) return this.value;
    const saved = await api.load();
    this.version = saved.version;
    this.value = { ...structuredClone(this.defaults), ...(saved.value || {}) };
    return this.value;
  }
  // mutate(value) changes the value in place; it is re-applied on conflict.
  update(mutate) {
    mutate(this.value);
    if (!api || this.version === null) return Promise.resolve();
    this.chain = this.chain.then(async () => {
      try {
        const saved = await api.save(this.value, this.version);
        this.version = saved.version;
      } catch {
        const latest = await api.load();
        this.version = latest.version;
        this.value = { ...structuredClone(this.defaults), ...(latest.value || {}) };
        mutate(this.value);
        const saved = await api.save(this.value, this.version);
        this.version = saved.version;
        this.listeners.forEach(fn => fn(this.value));
      }
    }).catch(error => console.warn("storage", error));
    return this.chain;
  }
  // Re-reads storage; resolves true when another writer changed it.
  async poll() {
    if (!api) return false;
    const latest = await api.load();
    if (latest.version === this.version) return false;
    this.version = latest.version;
    this.value = { ...structuredClone(this.defaults), ...(latest.value || {}) };
    return true;
  }
}

export function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(ms) {
  const d = new Date(ms), now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return d.toLocaleTimeString("en", { hour: "numeric", minute: "2-digit" });
  return d.toLocaleDateString("en", { month: "short", day: "numeric", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" });
}

// Minimal Markdown for model output: headings, bullets, bold, italics, code.
export function renderMarkdown(text) {
  const esc = s => s.replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const inline = s => esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  const out = [];
  let list = null;
  for (const raw of String(text).split("\n")) {
    const line = raw.trimEnd();
    const bullet = /^\s*(?:[-*•]|\d+[.)])\s+(.*)$/.exec(line);
    if (bullet) {
      const ordered = /^\s*\d/.test(line);
      if (!list || list !== (ordered ? "ol" : "ul")) { if (list) out.push(`</${list}>`); list = ordered ? "ol" : "ul"; out.push(`<${list}>`); }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    if (list) { out.push(`</${list}>`); list = null; }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) out.push(`<h${heading[1].length + 2}>${inline(heading[2])}</h${heading[1].length + 2}>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push(`</${list}>`);
  return out.join("");
}
