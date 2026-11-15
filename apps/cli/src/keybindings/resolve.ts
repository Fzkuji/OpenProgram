/**
 * Defaults plus the person's overrides from `<state dir>/keybindings.json`.
 *
 * The file is a flat object, `{ "app.toolsExpand": "ctrl+o" }`; a value
 * may be one key, a list of keys, or `"none"` to switch the action off.
 * A key the person binds is taken away from any default action in the
 * same context (their choice wins); a problem in the file never stops
 * the TUI — each one becomes a warning line and the rest still applies.
 */
import { existsSync, readFileSync } from 'fs';
import { join } from 'path';
import { tuiStateDir } from '../utils/stateDir.js';
import type { Key } from '../runtime/ink/events/input-event.js';
import {
  ACTIONS,
  ACTION_IDS,
  formatKeySpec,
  keyMatches,
  parseKeySpec,
  sameKey,
  type KeyContext,
  type KeySpec,
} from './table.js';

export interface ResolvedBindings {
  byAction: Record<string, KeySpec[]>;
  warnings: string[];
  /** The file the overrides came from, when it exists. */
  source?: string;
}

export const KEYBINDINGS_FILE = 'keybindings.json';

export function keybindingsPath(stateDir: string = tuiStateDir()): string {
  return join(stateDir, KEYBINDINGS_FILE);
}

const contextOf = new Map(ACTIONS.map((a) => [a.id, a.context]));

/** Contexts whose keys reach the same handler path. */
const overlapping = (a: KeyContext, b: KeyContext): boolean =>
  a === b || (a !== 'queue' && b !== 'queue');

export function defaultBindings(): ResolvedBindings {
  const byAction: Record<string, KeySpec[]> = {};
  for (const action of ACTIONS) {
    byAction[action.id] = action.defaults.map((k) => parseKeySpec(k)!).filter(Boolean);
  }
  return { byAction, warnings: [] };
}

export function resolveBindings(overrides: unknown): ResolvedBindings {
  const resolved = defaultBindings();
  if (overrides === undefined || overrides === null) return resolved;
  if (typeof overrides !== 'object' || Array.isArray(overrides)) {
    resolved.warnings.push('keybindings: the file must hold one object of action → key');
    return resolved;
  }
  const userKeys: Array<{ action: string; spec: KeySpec }> = [];
  for (const [action, value] of Object.entries(overrides as Record<string, unknown>)) {
    if (action.startsWith('$')) continue; // "$schema" and comments
    if (!ACTION_IDS.has(action)) {
      resolved.warnings.push(`keybindings: unknown action "${action}"`);
      continue;
    }
    const def = ACTIONS.find((a) => a.id === action)!;
    if (def.reserved) {
      resolved.warnings.push(`keybindings: "${action}" cannot be changed`);
      continue;
    }
    const raw = typeof value === 'string' ? [value] : Array.isArray(value) ? value : null;
    if (!raw || raw.some((v) => typeof v !== 'string')) {
      resolved.warnings.push(`keybindings: "${action}" needs a key, a list of keys, or "none"`);
      continue;
    }
    const specs: KeySpec[] = [];
    let ok = true;
    for (const text of raw as string[]) {
      if (text.trim().toLowerCase() === 'none') continue;
      const spec = parseKeySpec(text);
      if (!spec) {
        resolved.warnings.push(`keybindings: "${action}": cannot read key "${text}"`);
        ok = false;
        break;
      }
      if (spec.name === 'c' && spec.ctrl && !spec.alt && !spec.shift) {
        resolved.warnings.push(`keybindings: "${action}": ctrl+c is reserved`);
        ok = false;
        break;
      }
      specs.push(spec);
    }
    if (!ok) continue;
    resolved.byAction[action] = specs;
    for (const spec of specs) userKeys.push({ action, spec });
  }
  // A key the person bound leaves every other action in an overlapping
  // context; two of their own bindings on one key are reported.
  const userActions = new Set(userKeys.map((u) => u.action));
  for (const { action, spec } of userKeys) {
    const ctx = contextOf.get(action)!;
    for (const other of ACTIONS) {
      if (other.id === action || !overlapping(ctx, other.context)) continue;
      const keys = resolved.byAction[other.id] ?? [];
      if (!keys.some((k) => sameKey(k, spec))) continue;
      if (userActions.has(other.id)) {
        const clash = `keybindings: ${formatKeySpec(spec)} is bound to both "${action}" and "${other.id}"`;
        const mirror = `keybindings: ${formatKeySpec(spec)} is bound to both "${other.id}" and "${action}"`;
        if (!resolved.warnings.includes(mirror)) resolved.warnings.push(clash);
        continue;
      }
      resolved.byAction[other.id] = keys.filter((k) => !sameKey(k, spec));
    }
  }
  return resolved;
}

/** Read and resolve the config file; a missing file is the defaults. */
export function loadBindings(path: string = keybindingsPath()): ResolvedBindings {
  if (!existsSync(path)) return defaultBindings();
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, 'utf8').replace(/^﻿/, ''));
  } catch (error) {
    const resolved = defaultBindings();
    resolved.warnings.push(`keybindings: ${path} is not valid JSON (${(error as Error).message})`);
    resolved.source = path;
    return resolved;
  }
  const resolved = resolveBindings(parsed);
  resolved.source = path;
  return resolved;
}

export function bindingMatches(
  bindings: ResolvedBindings,
  action: string,
  input: string,
  key: Key,
): boolean {
  return (bindings.byAction[action] ?? []).some((spec) => keyMatches(spec, input, key));
}

/** "ctrl+o", "enter / e", or "" when the action is switched off. */
export function bindingLabel(bindings: ResolvedBindings, action: string): string {
  return (bindings.byAction[action] ?? []).map(formatKeySpec).join(' / ');
}

/** The rows `?` shows: every action with its active keys. */
export function helpRows(bindings: ResolvedBindings): Array<{ id: string; keys: string; description: string }> {
  return ACTIONS.map((a) => ({
    id: a.id,
    keys: bindingLabel(bindings, a.id) || 'off',
    description: a.description,
  }));
}
