/**
 * Long-paste auto-attach store for the chat composer.
 *
 * When the user pastes a chunk of text larger than the threshold,
 * we fold it into a numbered placeholder token in the input and stash
 * the real content here. The composer renders a small chip row so the
 * user can see / remove pastes; on submit we expand every token back
 * into the outgoing message body so the LLM receives the full text.
 *
 * Modeled on claude-code's pasteStore + inputPaste truncation logic
 * (``references/claude-code-leaked/src/components/PromptInput/inputPaste.ts``)
 * but simplified: claude-code persists pastes to disk (multi-process
 * + session resume); we keep them in-memory per browser tab, which is
 * sufficient because the web composer is a single client lifetime.
 *
 * Storage shape is shared between web and the future TUI port — keep
 * the format minimal so the same content-addressable token works on
 * both sides.
 */

export interface PastedEntry {
  /** Monotonic per-session id. Matches the ``#N`` in the token. */
  id: number;
  /** Verbatim pasted content. */
  content: string;
  /** Pre-computed line count for the placeholder display. */
  numLines: number;
}

/** Characters above which a paste auto-attaches. ~30 lines of 80
 *  columns; matches claude-code's PASTE_THRESHOLD. */
export const LONG_PASTE_THRESHOLD = 2000;

const TOKEN_RE = /\[Pasted #(\d+) \+(\d+) lines\]/g;

class PasteStore {
  private nextId = 1;
  private map = new Map<number, PastedEntry>();
  // Subscriber list so the chip row re-renders on add/remove. The
  // composer registers exactly one subscriber inside a useEffect.
  private subs = new Set<() => void>();

  add(content: string): PastedEntry {
    const id = this.nextId++;
    const numLines = content.split("\n").length;
    const entry: PastedEntry = { id, content, numLines };
    this.map.set(id, entry);
    this.notify();
    return entry;
  }

  remove(id: number): void {
    if (this.map.delete(id)) this.notify();
  }

  get(id: number): PastedEntry | undefined {
    return this.map.get(id);
  }

  /** Ordered by id so the chip row is stable. */
  list(): PastedEntry[] {
    return Array.from(this.map.values()).sort((a, b) => a.id - b.id);
  }

  /** Subscribe to add/remove events. Returns an unsubscribe fn. */
  subscribe(fn: () => void): () => void {
    this.subs.add(fn);
    return () => this.subs.delete(fn);
  }

  private notify(): void {
    this.subs.forEach((s) => s());
  }
}

export const pasteStore = new PasteStore();

// Expose to window for diagnostics (Chrome MCP probing, browser
// devtools). Harmless in prod — it's just a reference to the same
// in-memory store the React composer reads.
if (typeof window !== "undefined") {
  (window as unknown as { __pasteStore?: PasteStore }).__pasteStore =
    pasteStore;
}

/** Format a placeholder token for an entry. The composer textarea
 *  displays this in place of the pasted content. */
export function placeholderToken(entry: PastedEntry): string {
  return `[Pasted #${entry.id} +${entry.numLines} lines]`;
}

/** Replace every ``[Pasted #N +M lines]`` token in ``text`` with the
 *  stored content. Used at submit time to inline pastes back into the
 *  outgoing message. Unknown ids (entry deleted before submit) are
 *  replaced with an empty string. */
export function expandPasteTokens(text: string): string {
  return text.replace(TOKEN_RE, (_match, idStr) => {
    const id = Number(idStr);
    const entry = pasteStore.get(id);
    return entry ? entry.content : "";
  });
}

/** Return the list of paste ids currently referenced by ``text``.
 *  Used to garbage-collect chips when the user manually deletes the
 *  token from the textarea. */
export function referencedPasteIds(text: string): Set<number> {
  const out = new Set<number>();
  let m: RegExpExecArray | null;
  // Reset lastIndex because TOKEN_RE is a shared /g regex.
  TOKEN_RE.lastIndex = 0;
  while ((m = TOKEN_RE.exec(text)) !== null) {
    out.add(Number(m[1]));
  }
  return out;
}
