import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync } from 'fs';
import { join, dirname } from 'path';
import { tuiStateDir } from './stateDir.js';

const MAX_HISTORY = 500;
const historyPath = (): string => join(tuiStateDir(), 'cli-history');

const ensureDir = (): void => {
  const dir = dirname(historyPath());
  if (!existsSync(dir)) {
    try {
      mkdirSync(dir, { recursive: true });
    } catch {
      // ignore — we'll fall back to in-memory only
    }
  }
};

export function loadHistory(): string[] {
  const path = historyPath();
  if (!existsSync(path)) return [];
  try {
    const raw = readFileSync(path, 'utf8');
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
    appendFileSync(historyPath(), line.replace(/\n/g, '\\n') + '\n');
  } catch {
    // best effort — no error to user
  }
}

export function trimHistoryFile(): void {
  // Periodically truncate the file so it doesn't grow unbounded.
  const path = historyPath();
  if (!existsSync(path)) return;
  try {
    const lines = loadHistory();
    if (lines.length <= MAX_HISTORY) return;
    writeFileSync(path, lines.slice(-MAX_HISTORY).join('\n') + '\n');
  } catch {
    // ignore
  }
}
