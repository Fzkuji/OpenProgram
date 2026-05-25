/**
 * Per-session composer-draft persistence for the TUI.
 *
 * Mirrors what the web composer does (``composerDrafts`` in
 * ``web/lib/session-store.ts``) but on disk: when the user quits the
 * TUI mid-typing or switches sessions, the unsent text is preserved.
 *
 * Layout: one JSON file at ``<state>/tui_drafts.json`` keyed by
 * session id. The ``"__new__"`` slot holds the draft typed before any
 * session was started — mirroring the same pseudo-key on the web side.
 *
 * Write strategy: on every save() we rewrite the whole blob. Cheap
 * because drafts are small; avoids needing partial-update locking
 * across the TUI's two long-lived processes (kit-host + repl).
 */
import { mkdirSync, readFileSync, writeFileSync } from 'fs';
import { dirname, join } from 'path';
import { homedir } from 'os';

const SCHEMA_VERSION = 1;
const NEW_KEY = '__new__';

interface DraftBlob {
  v: number;
  drafts: Record<string, string>;
}

function path(): string {
  // Match openprogram.paths.get_state_dir() default (the Python side
  // resolves to ~/.agentic) so a single source of truth holds across
  // languages. Override via OPENPROGRAM_STATE_DIR env if the user has
  // pointed the worker somewhere custom.
  const stateDir = process.env.OPENPROGRAM_STATE_DIR
    || process.env.AGENTIC_DIR
    || join(homedir(), '.agentic');
  return join(stateDir, 'tui_drafts.json');
}

function read(): DraftBlob {
  try {
    const raw = readFileSync(path(), 'utf-8');
    const parsed = JSON.parse(raw) as DraftBlob;
    if (parsed && parsed.v === SCHEMA_VERSION
        && parsed.drafts && typeof parsed.drafts === 'object') {
      return parsed;
    }
  } catch {
    /* missing / corrupt — fall through to empty */
  }
  return { v: SCHEMA_VERSION, drafts: {} };
}

function write(blob: DraftBlob): void {
  try {
    mkdirSync(dirname(path()), { recursive: true });
    writeFileSync(path(), JSON.stringify(blob, null, 2), 'utf-8');
  } catch {
    /* best-effort; don't crash the UI over a draft write */
  }
}

/** Return the draft text for ``sessionId`` (or the ``__new__`` slot
 *  when ``sessionId`` is undefined / null). Empty string if none. */
export function getDraft(sessionId: string | null | undefined): string {
  const key = sessionId || NEW_KEY;
  return read().drafts[key] ?? '';
}

/** Persist the draft text for ``sessionId``. Empty strings prune the
 *  entry so the file doesn't grow unboundedly. */
export function setDraft(sessionId: string | null | undefined,
                         value: string): void {
  const key = sessionId || NEW_KEY;
  const blob = read();
  if (value) {
    blob.drafts[key] = value;
  } else {
    delete blob.drafts[key];
  }
  write(blob);
}

/** Drop the entry for ``sessionId``. Called after a successful send
 *  so the next typing-around starts clean. */
export function clearDraft(sessionId: string | null | undefined): void {
  setDraft(sessionId, '');
}
