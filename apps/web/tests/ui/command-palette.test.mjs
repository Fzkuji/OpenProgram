import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

// The sidebar helpers module pulls in the runtime bridge; the palette only
// needs its activity timestamp.
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/components/sidebar/sessions/helpers") {
      return {
        url: `data:text/javascript,${encodeURIComponent("export const activityTs = (c) => c.updated_at || c.created_at || 0;")}`,
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});

const { isPaletteShortcut, paletteSessions, PALETTE_SESSION_LIMIT } = await import("../../lib/command-palette.ts");

const key = (overrides = {}) => ({
  key: "k", metaKey: false, ctrlKey: false, altKey: false, shiftKey: false, isComposing: false, target: null,
  ...overrides,
});

test("Cmd+K on macOS and Ctrl+K elsewhere open the palette", () => {
  assert.equal(isPaletteShortcut(key({ metaKey: true }), true), true);
  assert.equal(isPaletteShortcut(key({ key: "K", metaKey: true }), true), true, "Caps Lock still matches");
  assert.equal(isPaletteShortcut(key({ ctrlKey: true }), true), false, "Ctrl+K stays an editing key on macOS");
  assert.equal(isPaletteShortcut(key({ ctrlKey: true }), false), true);
  assert.equal(isPaletteShortcut(key({ metaKey: true }), false), false);
});

test("modified, composing and terminal key presses are left alone", () => {
  assert.equal(isPaletteShortcut(key({ metaKey: true, shiftKey: true }), true), false);
  assert.equal(isPaletteShortcut(key({ metaKey: true, altKey: true }), true), false);
  assert.equal(isPaletteShortcut(key({ metaKey: true, isComposing: true }), true), false);
  assert.equal(isPaletteShortcut(key({ key: "j", metaKey: true }), true), false);
  const inTerminal = { closest: (selector) => (selector === ".xterm" ? {} : null) };
  assert.equal(isPaletteShortcut(key({ metaKey: true, target: inTerminal }), true), false,
    "Cmd+K clears a focused terminal instead");
  const elsewhere = { closest: () => null };
  assert.equal(isPaletteShortcut(key({ metaKey: true, target: elsewhere }), true), true);
});

test("sessions are unarchived and newest first, capped", () => {
  const listed = paletteSessions([
    { id: "old", title: "old", updated_at: 10 },
    { id: "archived", title: "gone", updated_at: 99, archived: true },
    { id: "new", title: "new", updated_at: 50 },
    { id: "created-only", title: "c", created_at: 30 },
  ]);
  assert.deepEqual(listed.map((c) => c.id), ["new", "created-only", "old"]);
  const many = Array.from({ length: PALETTE_SESSION_LIMIT + 20 }, (_, i) => ({ id: `s${i}`, title: "", updated_at: i }));
  const capped = paletteSessions(many);
  assert.equal(capped.length, PALETTE_SESSION_LIMIT);
  assert.equal(capped[0].id, `s${PALETTE_SESSION_LIMIT + 19}`);
});
