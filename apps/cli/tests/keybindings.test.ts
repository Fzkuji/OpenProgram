import { mkdtempSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { describe, expect, it } from 'vitest';

import { ACTIONS, formatKeySpec, keyMatches, parseKeySpec } from '../src/keybindings/table.js';
import {
  bindingLabel,
  bindingMatches,
  defaultBindings,
  helpRows,
  loadBindings,
  resolveBindings,
} from '../src/keybindings/resolve.js';
import type { Key } from '../src/runtime/ink/events/input-event.js';

const key = (over: Partial<Key> = {}): Key => ({
  upArrow: false, downArrow: false, leftArrow: false, rightArrow: false,
  pageDown: false, pageUp: false, wheelUp: false, wheelDown: false,
  home: false, end: false, return: false, escape: false, ctrl: false,
  shift: false, fn: false, tab: false, backspace: false, delete: false,
  meta: false, super: false, ...over,
});

describe('key specs', () => {
  it('parses modifiers, names and aliases', () => {
    expect(parseKeySpec('ctrl+o')).toEqual({ name: 'o', ctrl: true, alt: false, shift: false });
    expect(parseKeySpec('Shift+Tab')).toEqual({ name: 'tab', ctrl: false, alt: false, shift: true });
    expect(parseKeySpec('meta+return')).toEqual({ name: 'enter', ctrl: false, alt: true, shift: false });
    expect(parseKeySpec('esc')?.name).toBe('escape');
    expect(parseKeySpec('?')?.name).toBe('?');
    expect(parseKeySpec('hyper+x')).toBeNull();
    expect(parseKeySpec('ctrl+notakey')).toBeNull();
  });

  it('matches input events', () => {
    expect(keyMatches(parseKeySpec('ctrl+o')!, 'o', key({ ctrl: true }))).toBe(true);
    expect(keyMatches(parseKeySpec('ctrl+o')!, 'o', key())).toBe(false);
    expect(keyMatches(parseKeySpec('shift+tab')!, '', key({ tab: true, shift: true }))).toBe(true);
    expect(keyMatches(parseKeySpec('tab')!, '', key({ tab: true, shift: true }))).toBe(false);
    // Escape always carries meta in this runtime; plain "escape" still matches.
    expect(keyMatches(parseKeySpec('escape')!, '', key({ escape: true, meta: true }))).toBe(true);
    expect(keyMatches(parseKeySpec('alt+enter')!, '', key({ return: true, meta: true }))).toBe(true);
    expect(keyMatches(parseKeySpec('enter')!, '', key({ return: true, meta: true }))).toBe(false);
    expect(keyMatches(parseKeySpec('?')!, '?', key({ shift: true }))).toBe(true);
  });

  it('formats keys for hints', () => {
    expect(formatKeySpec(parseKeySpec('up')!)).toBe('↑');
    expect(formatKeySpec(parseKeySpec('ctrl+shift+p')!)).toBe('ctrl+shift+p');
  });
});

describe('resolved bindings', () => {
  it('defaults cover every action', () => {
    const resolved = defaultBindings();
    for (const action of ACTIONS) expect(resolved.byAction[action.id]!.length).toBeGreaterThan(0);
    expect(bindingLabel(resolved, 'app.toolsExpand')).toBe('ctrl+o');
  });

  it('an override replaces the default and takes the key from other actions', () => {
    const resolved = resolveBindings({ 'app.toolsExpand': 'ctrl+k' });
    expect(resolved.warnings).toEqual([]);
    expect(bindingMatches(resolved, 'app.toolsExpand', 'k', key({ ctrl: true }))).toBe(true);
    expect(bindingMatches(resolved, 'app.toolsExpand', 'o', key({ ctrl: true }))).toBe(false);
    // The palette lost ctrl+k to the person's own choice.
    expect(bindingLabel(resolved, 'app.commandPalette')).toBe('');
  });

  it('"none" switches an action off and lists of keys are kept', () => {
    const resolved = resolveBindings({ 'app.commandPalette': 'none', 'queue.remove': ['d', 'delete'] });
    expect(bindingLabel(resolved, 'app.commandPalette')).toBe('');
    expect(bindingLabel(resolved, 'queue.remove')).toBe('d / delete');
    expect(helpRows(resolved).find((r) => r.id === 'app.commandPalette')?.keys).toBe('off');
  });

  it('problems become warnings and never stop the rest', () => {
    const resolved = resolveBindings({
      'no.such.action': 'ctrl+x',
      'app.stopOrExit': 'ctrl+q',
      'app.help': 'hyper+h',
      'app.toolsExpand': 'ctrl+c',
      'input.newline': 42,
      'app.commandPalette': 'ctrl+p',
      'app.contextSearch': 'ctrl+p',
      $schema: 'ignored',
    });
    expect(resolved.warnings).toEqual([
      'keybindings: unknown action "no.such.action"',
      'keybindings: "app.stopOrExit" cannot be changed',
      'keybindings: "app.help": cannot read key "hyper+h"',
      'keybindings: "app.toolsExpand": ctrl+c is reserved',
      'keybindings: "input.newline" needs a key, a list of keys, or "none"',
      'keybindings: ctrl+p is bound to both "app.commandPalette" and "app.contextSearch"',
    ]);
    expect(bindingLabel(resolved, 'app.toolsExpand')).toBe('ctrl+o');
    expect(bindingLabel(resolved, 'app.commandPalette')).toBe('ctrl+p');
  });

  it('a queue key does not take the same key from the input', () => {
    const resolved = resolveBindings({ 'queue.edit': 'up' });
    expect(bindingLabel(resolved, 'input.historyPrev')).toBe('↑');
  });

  it('reads the file from the state dir and reports bad JSON', () => {
    const dir = mkdtempSync(join(tmpdir(), 'op-keys-'));
    expect(loadBindings(join(dir, 'missing.json')).warnings).toEqual([]);
    const good = join(dir, 'good.json');
    writeFileSync(good, '﻿{"app.toolsExpand": "ctrl+t"}');
    const loaded = loadBindings(good);
    expect(loaded.source).toBe(good);
    expect(bindingLabel(loaded, 'app.toolsExpand')).toBe('ctrl+t');
    const bad = join(dir, 'bad.json');
    writeFileSync(bad, '{nope');
    const broken = loadBindings(bad);
    expect(broken.warnings[0]).toMatch(/is not valid JSON/);
    expect(bindingLabel(broken, 'app.toolsExpand')).toBe('ctrl+o');
  });
});
