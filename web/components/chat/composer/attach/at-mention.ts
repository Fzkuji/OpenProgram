/**
 * ``@file`` mention helpers for the chat composer.
 *
 * Two pieces:
 *
 *   * :func:`findAtToken` — locate an ``@partial`` token immediately
 *     before the caret. Port of ``cli/src/utils/fileCompletions.ts``
 *     so web + tui agree on what counts as an open mention.
 *   * :func:`expandAtMentions` — at submit time, replace each
 *     ``@path/to/file`` token with the file's content inline. The
 *     LLM receives the same shape as if the user had pasted the file
 *     manually, so no backend protocol change is needed.
 *
 * Search + read both go through the worker's HTTP endpoints
 * (``/api/file-search`` and ``/api/file-read``) — see
 * ``openprogram/webui/routes/file_search.py``.
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
  /** Paths that were successfully inlined. */
  expanded: string[];
  /** Paths that failed to read (kept verbatim in the output). */
  missing: string[];
}

/** Replace every ``@path`` token in ``text`` with the file's content,
 *  fetched from the worker's ``/api/file-read`` endpoint. Paths that
 *  fail (404 / outside root / network error) are left as the
 *  original ``@path`` token and reported via :attr:`ExpandResult.missing`. */
export async function expandAtMentions(
  text: string,
  root: string | null,
): Promise<ExpandResult> {
  // Collect unique paths first so the same @path mentioned twice
  // only triggers one fetch.
  const seen = new Set<string>();
  SUBMIT_TOKEN_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = SUBMIT_TOKEN_RE.exec(text)) !== null) {
    seen.add(match[2]);
  }
  if (seen.size === 0) {
    return { text, expanded: [], missing: [] };
  }
  const fetched = new Map<string, { ok: true; content: string }
                                  | { ok: false }>();
  await Promise.all(
    Array.from(seen).map(async (path) => {
      try {
        const q = new URLSearchParams({ path });
        if (root) q.set("root", root);
        const r = await fetch(`/api/file-read?${q.toString()}`);
        if (!r.ok) {
          fetched.set(path, { ok: false });
          return;
        }
        const d = (await r.json()) as {
          path: string; content: string; truncated?: boolean;
        };
        const suffix = d.truncated ? "\n[...truncated...]" : "";
        fetched.set(path, { ok: true, content: d.content + suffix });
      } catch {
        fetched.set(path, { ok: false });
      }
    }),
  );

  const expanded: string[] = [];
  const missing: string[] = [];
  SUBMIT_TOKEN_RE.lastIndex = 0;
  const out = text.replace(SUBMIT_TOKEN_RE, (_full, lead: string, path: string) => {
    const got = fetched.get(path);
    if (got && got.ok) {
      expanded.push(path);
      // Render as a verbatim file block so the LLM has structure to
      // anchor on. ``lead`` preserves the leading whitespace / line
      // break the user typed before the mention.
      return `${lead}<file path="${path}">\n${got.content}\n</file>`;
    }
    missing.push(path);
    return `${lead}@${path}`;
  });
  return { text: out, expanded, missing };
}
