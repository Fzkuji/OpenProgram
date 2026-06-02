/**
 * ``@file`` mention helpers for the chat composer.
 *
 * Two pieces:
 *
 *   * :func:`findAtToken` — locate an ``@partial`` token immediately
 *     before the caret. Port of ``cli/src/utils/fileCompletions.ts``
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
 * ``openprogram/webui/routes/file_search.py``. ``file-resolve`` only
 * stats the file (absolute path + size); it never reads the bytes.
 */

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
 *  whitespace; path characters extend until whitespace. */
const SUBMIT_TOKEN_RE = /(^|\s)@([^\s@][^\s@]*)/g;

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
  root: string | null,
): Promise<ExpandResult> {
  // Collect unique paths first so the same @path mentioned twice
  // only triggers one resolve.
  const seen = new Set<string>();
  SUBMIT_TOKEN_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = SUBMIT_TOKEN_RE.exec(text)) !== null) {
    seen.add(match[2]);
  }
  if (seen.size === 0) {
    return { text, expanded: [], missing: [] };
  }
  const resolved = new Map<string, { ok: true; abs: string; size: number }
                                  | { ok: false }>();
  await Promise.all(
    Array.from(seen).map(async (path) => {
      try {
        const q = new URLSearchParams({ path });
        if (root) q.set("root", root);
        const r = await fetch(`/api/file-resolve?${q.toString()}`);
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
  const out = text.replace(SUBMIT_TOKEN_RE, (_full, lead: string, path: string) => {
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
    return `${lead}@${path}`;
  });
  return { text: out, expanded, missing };
}
