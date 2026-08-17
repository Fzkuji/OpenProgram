import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync } from 'fs';
import { homedir } from 'os';
import { join, dirname } from 'path';

const HISTORY_PATH = join(homedir(), '.openprogram', 'cli-history');
const MAX_HISTORY = 500;

const ensureDir = (): void => {
  const dir = dirname(HISTORY_PATH);
  if (!existsSync(dir)) {
    try {
      mkdirSync(dir, { recursive: true });
    } catch {
      // ignore — we'll fall back to in-memory only
    }
  }
};

export function loadHistory(): string[] {
  if (!existsSync(HISTORY_PATH)) return [];
  try {
    const raw = readFileSync(HISTORY_PATH, 'utf8');
    return raw
      .split('\n')
      .map((s) => s.trimEnd())
      .filter((s) => s.length > 0)
      .slice(-MAX_HISTORY);
  } catch {
    return [];
  }
}

export function appendHistory(line: string): void {
  if (!line.trim()) return;
  ensureDir();
  try {
    appendFileSync(HISTORY_PATH, line.replace(/\n/g, '\\n') + '\n');
  } catch {
    // best effort — no error to user
  }
}

export function trimHistoryFile(): void {
  // Periodically truncate the file so it doesn't grow unbounded.
  if (!existsSync(HISTORY_PATH)) return;
  try {
    const lines = loadHistory();
    if (lines.length <= MAX_HISTORY) return;
    writeFileSync(HISTORY_PATH, lines.slice(-MAX_HISTORY).join('\n') + '\n');
  } catch {
    // ignore
  }
}
