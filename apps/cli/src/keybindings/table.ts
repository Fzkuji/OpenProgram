/**
 * The TUI's keyboard actions, declared once.
 *
 * Every rebindable key the terminal UI reacts to is an action here: its
 * id, where it applies, its default keys and the sentence the `?` help
 * shows. Handlers ask `matches(action, input, key)` instead of testing
 * raw keys, so the help, the config file and the behaviour cannot drift.
 * The shape (a flat `action -> keys` map, `"none"` to disable) follows
 * Pi's keybindings.json and opencode's `keybinds`; reserved keys follow
 * Claude Code (Ctrl+C keeps its stop / double-press-exit meaning).
 * See docs/reference/design/cli/tui-upgrade.html.
 */
import type { Key } from '../runtime/ink/events/input-event.js';

/** Where an action is live. `global` and `input` both see every key
 *  while the prompt is focused, so they are checked together for
 *  conflicts; `queue` only while a queued message is selected. */
export type KeyContext = 'global' | 'input' | 'queue';

export interface ActionDef {
  id: string;
  context: KeyContext;
  defaults: string[];
  description: string;
  /** Shown in help but cannot be changed from the config file. */
  reserved?: boolean;
}

const IMAGE_PASTE = process.platform === 'win32' ? 'alt+v' : 'ctrl+v';

export const ACTIONS: readonly ActionDef[] = [
  { id: 'app.interrupt', context: 'global', defaults: ['escape'], description: 'stop a running turn' },
  { id: 'app.stopOrExit', context: 'global', defaults: ['ctrl+c'], description: 'stop a running turn · twice while idle to exit', reserved: true },
  { id: 'app.commandPalette', context: 'global', defaults: ['ctrl+k'], description: 'open command palette' },
  { id: 'app.permissionCycle', context: 'global', defaults: ['shift+tab'], description: 'cycle permission modes' },
  { id: 'app.toolsExpand', context: 'global', defaults: ['ctrl+o'], description: 'expand or collapse tool output' },
  { id: 'app.help', context: 'input', defaults: ['?'], description: 'show keyboard shortcuts (empty input)' },
  { id: 'app.contextSearch', context: 'input', defaults: ['ctrl+r'], description: 'search saved context' },
  { id: 'input.submit', context: 'input', defaults: ['enter'], description: 'send · queue while a turn runs' },
  { id: 'input.newline', context: 'input', defaults: ['alt+enter'], description: 'insert a newline' },
  { id: 'input.clear', context: 'input', defaults: ['escape'], description: 'clear input (idle)' },
  { id: 'input.lineStart', context: 'input', defaults: ['ctrl+a'], description: 'start of input' },
  { id: 'input.lineEnd', context: 'input', defaults: ['ctrl+e'], description: 'end of input · accept suggestion' },
  { id: 'input.deleteWord', context: 'input', defaults: ['ctrl+w'], description: 'delete previous word' },
  { id: 'input.historyPrev', context: 'input', defaults: ['up'], description: 'previous prompt · select queued message' },
  { id: 'input.historyNext', context: 'input', defaults: ['down'], description: 'next prompt' },
  { id: 'input.pasteImage', context: 'input', defaults: [IMAGE_PASTE], description: 'attach an image from the clipboard' },
  { id: 'queue.edit', context: 'queue', defaults: ['enter', 'e'], description: 'edit the selected queued message' },
  { id: 'queue.remove', context: 'queue', defaults: ['x', 'delete', 'backspace'], description: 'remove the selected queued message' },
  { id: 'queue.prev', context: 'queue', defaults: ['up'], description: 'select the earlier queued message' },
  { id: 'queue.next', context: 'queue', defaults: ['down'], description: 'select the later queued message' },
  { id: 'queue.leave', context: 'queue', defaults: ['escape'], description: 'back to the input' },
];

export const ACTION_IDS = new Set(ACTIONS.map((a) => a.id));

/** One parsed key: a key name plus modifiers. */
export interface KeySpec {
  name: string;
  ctrl: boolean;
  alt: boolean;
  shift: boolean;
}

const NAMED = new Set([
  'escape', 'enter', 'tab', 'up', 'down', 'left', 'right', 'backspace',
  'delete', 'home', 'end', 'pageup', 'pagedown', 'space',
]);
const ALIASES: Record<string, string> = {
  esc: 'escape', return: 'enter', del: 'delete', pgup: 'pageup', pgdn: 'pagedown',
  meta: 'alt', option: 'alt', opt: 'alt', control: 'ctrl',
};

/** `ctrl+shift+x` → KeySpec; `null` when it cannot be read. */
export function parseKeySpec(raw: string): KeySpec | null {
  const text = raw.trim().toLowerCase();
  if (!text) return null;
  // A lone "+" or "?" is a key, not a separator.
  const parts = text === '+' ? ['+'] : text.split('+').map((p) => ALIASES[p] ?? p);
  const spec: KeySpec = { name: '', ctrl: false, alt: false, shift: false };
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i]!;
    const last = i === parts.length - 1;
    if (!last) {
      if (part === 'ctrl') spec.ctrl = true;
      else if (part === 'alt') spec.alt = true;
      else if (part === 'shift') spec.shift = true;
      else return null;
      continue;
    }
    if (NAMED.has(part) || [...part].length === 1) spec.name = part;
    else return null;
  }
  return spec.name ? spec : null;
}

/** The key name an input event carries, in KeySpec terms. */
function eventName(input: string, key: Key): string {
  if (key.escape) return 'escape';
  if (key.return) return 'enter';
  if (key.tab) return 'tab';
  if (key.upArrow) return 'up';
  if (key.downArrow) return 'down';
  if (key.leftArrow) return 'left';
  if (key.rightArrow) return 'right';
  if (key.backspace) return 'backspace';
  if (key.delete) return 'delete';
  if (key.home) return 'home';
  if (key.end) return 'end';
  if (key.pageUp) return 'pageup';
  if (key.pageDown) return 'pagedown';
  if (input === ' ') return 'space';
  return input.toLowerCase();
}

export function keyMatches(spec: KeySpec, input: string, key: Key): boolean {
  if (eventName(input, key) !== spec.name) return false;
  if (spec.ctrl !== key.ctrl) return false;
  // Escape always carries `meta` in this runtime, so alt only counts
  // when the spec asks for it on a non-escape key.
  const alt = spec.name === 'escape' ? false : key.meta;
  if (spec.alt !== alt) return false;
  // Uppercase letters set shift on their own; a printable spec without
  // shift (like "?") matches whatever shift its character needed.
  if (spec.shift && !key.shift) return false;
  if (!spec.shift && key.shift && NAMED.has(spec.name)) return false;
  return true;
}

const GLYPHS: Record<string, string> = { up: '↑', down: '↓', left: '←', right: '→', escape: 'esc' };

export function formatKeySpec(spec: KeySpec): string {
  const mods = [spec.ctrl ? 'ctrl' : '', spec.alt ? 'alt' : '', spec.shift ? 'shift' : ''].filter(Boolean);
  return [...mods, GLYPHS[spec.name] ?? spec.name].join('+');
}

export function sameKey(a: KeySpec, b: KeySpec): boolean {
  return a.name === b.name && a.ctrl === b.ctrl && a.alt === b.alt && a.shift === b.shift;
}
