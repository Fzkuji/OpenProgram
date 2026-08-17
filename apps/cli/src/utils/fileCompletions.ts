import { readdirSync, statSync } from 'fs';
import { join, basename, relative, resolve } from 'path';

export interface FileMatch {
  /** Path relative to cwd, e.g. "src/index.tsx". */
  path: string;
  /** True for directories so the caller can append / on insert. */
  isDir: boolean;
}

const SKIP_DIRS = new Set([
  'node_modules',
  '.git',
  'dist',
  '.next',
  '__pycache__',
  '.venv',
  'venv',
  '.cache',
  'target',
  'build',
]);

/**
 * Walk the working tree breadth-first and return paths whose basename or
 * relative path contains the needle (case-insensitive).
 *
 * Capped at `limit` matches and `maxScan` total files visited so this stays
 * cheap even in large repos. Hidden directories and well-known noisy
 * folders (node_modules, .git, dist, …) are skipped entirely.
 */
export function fileCompletions(
  needle: string,
  cwd: string = process.cwd(),
  limit = 12,
  maxScan = 5000,
): FileMatch[] {
  const lower = needle.toLowerCase();
  const out: FileMatch[] = [];
  let scanned = 0;
  const queue: string[] = [cwd];

  while (queue.length > 0 && out.length < limit && scanned < maxScan) {
    const dir = queue.shift()!;
    let entries: string[];
    try {
      entries = readdirSync(dir);
    } catch {
      continue;
    }
    for (const name of entries) {
      if (name.startsWith('.')) continue;
      if (SKIP_DIRS.has(name)) continue;
      const full = join(dir, name);
      let st: ReturnType<typeof statSync>;
      try {
        st = statSync(full);
      } catch {
        continue;
      }
      scanned++;
      const rel = relative(cwd, full);
      const isDir = st.isDirectory();
      const lowerRel = rel.toLowerCase();
      const lowerBase = basename(rel).toLowerCase();
      if (!lower || lowerBase.includes(lower) || lowerRel.includes(lower)) {
        out.push({ path: rel, isDir });
        if (out.length >= limit) break;
      }
      if (isDir) queue.push(full);
    }
  }
  return out;
}

/**
 * Find an `@partial` token anywhere before the cursor and return the start
 * index + the partial text. Returns null if there's no `@` before cursor or
 * if the previous char is alnum (not a fresh @ trigger).
 */
export function findAtToken(
  value: string,
  cursor: number,
): { start: number; partial: string } | null {
  let i = cursor - 1;
  // Walk back over path-like chars to find the @ that starts this token.
  while (i >= 0) {
    const c = value[i];
    if (c === '@') {
      // The @ should not be part of an email-like token. Require start of
      // string or a whitespace char before it.
      const before = i > 0 ? value[i - 1] : ' ';
      if (before === undefined || /\s/.test(before)) {
        return { start: i, partial: value.slice(i + 1, cursor) };
      }
      return null;
    }
    if (c === ' ' || c === '\n' || c === '\t') return null;
    i--;
  }
  return null;
}

/** Resolve a relative path back to absolute; useful for file viewers. */
export const absolutize = (rel: string, cwd: string = process.cwd()) => resolve(cwd, rel);
