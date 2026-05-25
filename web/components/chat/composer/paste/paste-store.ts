/**
 * Long-paste auto-attach store for the chat composer.
 *
 * When the user pastes a chunk of text larger than the threshold,
 * we fold it into a numbered placeholder token in the input and stash
 * the real content here. The composer renders a small chip row so the
 * user can see / remove pastes; on submit we expand every token back
 * into the outgoing message body so the LLM receives the full text.
 *
 * Persistence: the store mirrors itself to ``localStorage`` so that a
 * paste token left in a per-session composer draft (see
 * ``session-store.ts``'s ``composerDrafts``) still resolves after a
 * tab reload. Without this the token survives but its content does
 * not, and submit silently turns it into an empty string. Single-
 * paste and total-store size caps keep us under the ~5 MB localStorage
 * quota; over the cap, the oldest pastes are evicted (LRU). Caller is
 * expected to check ``missingPasteIds()`` before submit so dropped
 * pastes surface as a warning instead of silent data loss.
 *
 * Modeled on claude-code's pasteStore + inputPaste truncation logic
 * (``references/claude-code-leaked/src/components/PromptInput/inputPaste.ts``).
 */

export interface PastedEntry {
  /** Monotonic per-tab id. Matches the ``#N`` in the token. */
  id: number;
  /** Verbatim pasted content. */
  content: string;
  /** Pre-computed line count for the placeholder display. */
  numLines: number;
}

/** Characters above which a paste auto-attaches. ~30 lines of 80
 *  columns; matches claude-code's PASTE_THRESHOLD. */
export const LONG_PASTE_THRESHOLD = 2000;

/** Don't persist individual pastes larger than this — they would
 *  dominate the localStorage budget. The paste still works in this
 *  tab; it just won't survive a reload. */
const PERSIST_PASTE_MAX = 1_000_000;

/** Total persisted-paste budget. localStorage has a per-origin quota
 *  (typically 5 MB); paste content shares it with composer drafts +
 *  everything else this app stores. */
const PERSIST_TOTAL_MAX = 2_000_000;

const STORAGE_KEY = "composerPasteStore";
const STORAGE_VERSION = 1;

interface PersistShape {
  v: number;
  nextId: number;
  entries: PastedEntry[];
}

/** Build a fresh regex each call — module-level /g regex sharing
 *  ``lastIndex`` is a classic JS footgun. Cheap to recompile. */
function tokenRegex(): RegExp {
  return /\[Pasted #(\d+) \+(\d+) lines\]/g;
}

class PasteStore {
  private nextId = 1;
  private map = new Map<number, PastedEntry>();
  // Subscriber list so the chip row re-renders on add/remove. The
  // composer registers exactly one subscriber inside a useEffect.
  private subs = new Set<() => void>();
  private hydrated = false;

  add(content: string): PastedEntry {
    this.ensureHydrated();
    const id = this.nextId++;
    const numLines = content.split("\n").length;
    const entry: PastedEntry = { id, content, numLines };
    this.map.set(id, entry);
    this.persist();
    this.notify();
    return entry;
  }

  remove(id: number): void {
    this.ensureHydrated();
    if (this.map.delete(id)) {
      this.persist();
      this.notify();
    }
  }

  get(id: number): PastedEntry | undefined {
    this.ensureHydrated();
    return this.map.get(id);
  }

  /** Ordered by id so the chip row is stable. */
  list(): PastedEntry[] {
    this.ensureHydrated();
    return Array.from(this.map.values()).sort((a, b) => a.id - b.id);
  }

  /** Subscribe to add/remove events. Returns an unsubscribe fn.
   *  Wrapped so the cleanup matches React's ``() => void`` contract
   *  (``Set.delete`` returns boolean). */
  subscribe(fn: () => void): () => void {
    this.subs.add(fn);
    return () => {
      this.subs.delete(fn);
    };
  }

  /** Drop every entry whose id is NOT in ``keep``. Used by session
   *  cleanup to GC pastes referenced only by a deleted draft. */
  retainOnly(keep: Set<number>): void {
    this.ensureHydrated();
    let changed = false;
    for (const id of Array.from(this.map.keys())) {
      if (!keep.has(id)) {
        this.map.delete(id);
        changed = true;
      }
    }
    if (changed) {
      this.persist();
      this.notify();
    }
  }

  /** Read localStorage on first access. We hydrate lazily so SSR
   *  module evaluation doesn't touch ``window``. */
  private ensureHydrated(): void {
    if (this.hydrated) return;
    this.hydrated = true;
    if (typeof window === "undefined") return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as PersistShape;
      if (!parsed || parsed.v !== STORAGE_VERSION
          || !Array.isArray(parsed.entries)) {
        return;
      }
      for (const e of parsed.entries) {
        if (typeof e?.id === "number" && typeof e?.content === "string"
            && typeof e?.numLines === "number") {
          this.map.set(e.id, e);
        }
      }
      const persistedNext = typeof parsed.nextId === "number" ? parsed.nextId : 0;
      const maxLoaded = this.map.size > 0
        ? Math.max(...Array.from(this.map.keys()))
        : 0;
      this.nextId = Math.max(persistedNext, maxLoaded + 1, 1);
    } catch {
      /* ignore — start empty */
    }
  }

  private persist(): void {
    if (typeof window === "undefined") return;
    try {
      // Pick entries to persist under the size cap. Drop any single
      // paste over PERSIST_PASTE_MAX. LRU-evict oldest ids until the
      // remaining total fits in PERSIST_TOTAL_MAX.
      const sorted = Array.from(this.map.values()).sort((a, b) => a.id - b.id);
      const eligible = sorted.filter(
        (e) => e.content.length <= PERSIST_PASTE_MAX,
      );
      let total = eligible.reduce((s, e) => s + e.content.length, 0);
      let start = 0;
      while (total > PERSIST_TOTAL_MAX && start < eligible.length) {
        total -= eligible[start].content.length;
        start++;
      }
      const payload: PersistShape = {
        v: STORAGE_VERSION,
        nextId: this.nextId,
        entries: eligible.slice(start),
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      /* localStorage may be unavailable (private mode) or full — fail
         silently; in-memory state still works for this session. */
    }
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
 *  preserved verbatim — the caller is responsible for detecting these
 *  via ``missingPasteIds`` before submitting so we don't silently
 *  drop data. */
export function expandPasteTokens(text: string): string {
  return text.replace(tokenRegex(), (match, idStr) => {
    const id = Number(idStr);
    const entry = pasteStore.get(id);
    return entry ? entry.content : match;
  });
}

/** Return the list of paste ids currently referenced by ``text``. */
export function referencedPasteIds(text: string): Set<number> {
  const out = new Set<number>();
  const re = tokenRegex();
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    out.add(Number(m[1]));
  }
  return out;
}

/** Return the subset of token ids in ``text`` that have no backing
 *  entry — i.e. pastes that were lost (reload past persistence cap,
 *  manual store clear, etc.). Empty set means submit is safe. */
export function missingPasteIds(text: string): Set<number> {
  const out = new Set<number>();
  const re = tokenRegex();
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const id = Number(m[1]);
    if (!pasteStore.get(id)) out.add(id);
  }
  return out;
}

/** Strip every paste token from ``text``. Used when the user wants
 *  to drop a draft that references missing pastes. */
export function stripPasteTokens(text: string): string {
  return text.replace(tokenRegex(), "");
}
