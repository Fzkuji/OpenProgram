/**
 * ``@file`` mention helpers for the chat composer.
 *
 * Two pieces:
 *
 *   * :func:`findAtToken` — locate an ``@partial`` token immediately
 *     before the caret. Port of ``apps/cli/src/utils/fileCompletions.ts``
 *     so web + tui agree on what counts as an open mention.
 *   * :func:`expandAtMentions` — at submit time, replace each
 *     ``@path/to/file`` token with a ``[attachment: name (type, KB) @
 *     <abs path>]`` mention. The file already lives on disk, so it is
 *     referenced by an ABSOLUTE PATH (resolved via the worker) and the
 *     agent reads it on demand with its ``read`` / ``pdf`` tools — the
 *     content is NOT inlined into the prompt. This matches how uploaded
 *     files are handled (saved to the session workdir, then referenced
 *     by path) so every attachment, whatever its source, reaches the
 *     model the same way: as a path, never a wall of inlined text.
 *
 * Search + resolve both go through the worker's HTTP endpoints
 * (``/api/file-search`` and ``/api/file-resolve``) — see
 * ``openprogram/interfaces/server/routes/files/file_search.py``. ``file-resolve``
 * only stats the file (absolute path + size); it never reads the bytes.
 * Both resolve paths in the chat's working directory — the folder its
 * agent runs in — which the worker finds from the chat (``MentionScope``).
 *
 * A path with whitespace is written quoted: ``@"docs/My Notes.md"``.
 */
import { PASTE_CLOSE, PASTE_OPEN } from "../../paste/paste-store.ts";

/** The chat a mention belongs to. The worker resolves `@path` in that
 *  chat's working directory: its worktree or project folder, else the
 *  default project's. A chat not created yet passes the project picked
 *  for it. */
export interface MentionScope {
  sessionId?: string | null;
  projectId?: string | null;
}

/** Query parameters naming the scope, for the file-search/-resolve calls. */
export function mentionScopeParams(scope: MentionScope | null | undefined, params: URLSearchParams): URLSearchParams {
  if (scope?.sessionId) params.set("session_id", scope.sessionId);
  if (scope?.projectId) params.set("project_id", scope.projectId);
  return params;
}

export interface AtToken {
  /** Character index in the value where the ``@`` sits. */
  start: number;
  /** Text between ``@`` and the caret, excluding the ``@`` itself. */
  partial: string;
}

/** Path characters allowed in a mention — same as the TUI port: any
 *  non-whitespace, non-``@`` char extends the token. */
const PATH_RE = /^[^\s@]$/;

export function findAtToken(value: string, cursor: number): AtToken | null {
  const plain = findPlainAtToken(value, cursor);
  if (plain) {
    if (!plain.partial.startsWith('"')) return plain;
    // ``@"partial`` with no closing quote yet is still open.
    const inner = plain.partial.slice(1);
    return inner.includes('"') ? null : { start: plain.start, partial: inner };
  }
  // An open quoted mention whose partial already holds whitespace.
  const at = value.lastIndexOf('@"', cursor - 2);
  if (at < 0) return null;
  const inner = value.slice(at + 2, cursor);
  if (/["\n]/.test(inner)) return null;
  if (at > 0 && !/\s/.test(value[at - 1])) return null;
  return { start: at, partial: inner };
}

function findPlainAtToken(value: string, cursor: number): AtToken | null {
  let i = cursor - 1;
  while (i >= 0) {
    const c = value[i];
    if (c === "@") {
      const before = i > 0 ? value[i - 1] : " ";
      if (before === undefined || /\s/.test(before)) {
        return { start: i, partial: value.slice(i + 1, cursor) };
      }
      return null;
    }
    if (!c || !PATH_RE.test(c)) return null;
    i--;
  }
  return null;
}

/** Regex for tokens consumed at submit. Same constraints as
 *  :func:`findAtToken`: ``@`` must be at start-of-string or after
 *  whitespace; path characters extend until whitespace, or the path is
 *  quoted (``@"a b/c.md"``) and runs to the closing quote. */
const SUBMIT_TOKEN_RE = /(^|\s)@(?:"([^"\n]+)"|([^\s@"][^\s@]*))/g;

/** At most this many distinct paths are looked up per message. */
export const MAX_MENTION_RESOLVES = 20;

/** Ranges a mention never expands in: pasted blocks and ``` fences, where
 *  ``@Override`` / ``@dataclass`` are code, not file references. */
function quietRanges(text: string): Array<[number, number]> {
  const ranges: Array<[number, number]> = [];
  const add = (open: string, close: string, fromLineStart: boolean) => {
    let i = 0;
    while (i < text.length) {
      let start = text.indexOf(open, i);
      while (fromLineStart && start > 0 && text[start - 1] !== "\n") start = text.indexOf(open, start + 1);
      if (start < 0) return;
      let end = text.indexOf(close, start + open.length);
      while (fromLineStart && end > 0 && text[end - 1] !== "\n") end = text.indexOf(close, end + 1);
      if (end < 0) { ranges.push([start, text.length]); return; }
      ranges.push([start, end + close.length]);
      i = end + close.length;
    }
  };
  add(PASTE_OPEN, PASTE_CLOSE, false);
  add("```", "```", true);
  return ranges;
}

function inQuiet(ranges: Array<[number, number]>, at: number): boolean {
  return ranges.some(([from, to]) => at >= from && at < to);
}

export interface ExpandResult {
  text: string;
  /** Paths that were successfully resolved to a mention. */
  expanded: string[];
  /** Paths that failed to resolve (kept verbatim in the output). */
  missing: string[];
}

/** Lower-cased extension (no dot) for the chip badge, or ``""``. */
function extOf(name: string): string {
  return name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
}

/** Replace every ``@path`` token in ``text`` with a path-reference
 *  ``[attachment: name (type, KB) @ <abs path>]`` mention. The absolute
 *  path is resolved via the worker's ``/api/file-resolve`` endpoint
 *  (stat only — the bytes are NOT read). The agent reads the file on
 *  demand. Paths that fail (404 / outside root / network error) are
 *  left as the original ``@path`` token and reported via
 *  :attr:`ExpandResult.missing`. */
export async function expandAtMentions(
  text: string,
  scope: MentionScope | null,
): Promise<ExpandResult> {
  // Collect unique paths first so the same @path mentioned twice
  // only triggers one resolve.
  const quiet = quietRanges(text);
  const seen = new Set<string>();
  SUBMIT_TOKEN_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = SUBMIT_TOKEN_RE.exec(text)) !== null) {
    if (inQuiet(quiet, match.index + match[1].length)) continue;
    if (seen.size >= MAX_MENTION_RESOLVES) break;
    seen.add(match[2] ?? match[3]);
  }
  if (seen.size === 0) {
    return { text, expanded: [], missing: [] };
  }
  const signal = AbortSignal.timeout(15_000);
  const resolved = new Map<string, { ok: true; abs: string; size: number }
                                  | { ok: false }>();
  await Promise.all(
    Array.from(seen).map(async (path) => {
      try {
        const q = mentionScopeParams(scope, new URLSearchParams({ path }));
        const r = await fetch(`/api/file-resolve?${q.toString()}`, { signal });
        if (!r.ok) {
          resolved.set(path, { ok: false });
          return;
        }
        const d = (await r.json()) as { path: string; size: number };
        resolved.set(path, { ok: true, abs: d.path, size: d.size });
      } catch {
        resolved.set(path, { ok: false });
      }
    }),
  );

  const expanded: string[] = [];
  const missing: string[] = [];
  SUBMIT_TOKEN_RE.lastIndex = 0;
  const out = text.replace(SUBMIT_TOKEN_RE, (full: string, lead: string, quoted: string | undefined,
                                            bare: string | undefined, offset: number) => {
    if (inQuiet(quiet, offset + lead.length)) return full;
    const path = quoted ?? bare!;
    // Past the lookup cap: left as typed, not reported missing.
    if (!seen.has(path)) return full;
    const got = resolved.get(path);
    if (got && got.ok) {
      expanded.push(path);
      // Path-reference mention: the agent reads ``got.abs`` on demand.
      // ``lead`` preserves the whitespace / line break the user typed
      // before the mention.
      const base = path.split("/").pop() || path;
      const kb = Math.max(1, Math.round(got.size / 1024));
      return `${lead}[attachment: ${base} (${extOf(base) || "file"}, ${kb} KB) @ ${got.abs}]`;
    }
    missing.push(path);
    return full;
  });
  return { text: out, expanded, missing };
}
