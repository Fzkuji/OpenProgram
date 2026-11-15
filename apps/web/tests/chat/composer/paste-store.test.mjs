/**
 * Long pastes are kept per window: one shared map rewritten whole by every
 * window let one window's write drop another's pastes, and windows handed
 * out the same ids. A draft moved to another window still finds its paste.
 */
import assert from "node:assert/strict";
import test from "node:test";

const storage = new Map();
globalThis.localStorage = {
  get length() { return storage.size; },
  key: (i) => [...storage.keys()][i] ?? null,
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, String(value)),
  removeItem: (key) => storage.delete(key),
};
globalThis.window = { openprogramDesktop: { isDesktop: true, windowId: "w1" } };

const { pasteStore, expandPasteTokens, placeholderToken, missingPasteIds } =
  await import("../../../components/chat/composer/paste/paste-store.ts");

test("a paste is saved under this window's own key with an id no other window hands out", () => {
  storage.set("composerPasteStore:w2", JSON.stringify({ v: 1, nextId: 2, entries: [{ id: 1, content: "other window", numLines: 1 }] }));
  const entry = pasteStore.add("mine\n".repeat(500));
  assert.ok(entry.id > 1_000_000_000, "time-based id");
  const saved = JSON.parse(storage.get("composerPasteStore:w1"));
  assert.deepEqual(saved.entries.map((e) => e.id), [entry.id]);
  assert.equal(JSON.parse(storage.get("composerPasteStore:w2")).entries[0].content, "other window",
    "another window's pastes are left alone");
  assert.equal(expandPasteTokens(placeholderToken(entry)), entry.content);
});

test("a draft moved here from another window resolves its paste and keeps it", () => {
  const moved = { id: 1_700_000_000_000_123, content: "moved\n".repeat(400), numLines: 401 };
  storage.set("composerPasteStore:w3", JSON.stringify({ v: 1, nextId: 0, entries: [moved] }));
  const token = placeholderToken(moved);
  assert.equal(missingPasteIds(token).size, 0);
  assert.equal(expandPasteTokens(token), moved.content);
  assert.ok(JSON.parse(storage.get("composerPasteStore:w1")).entries.some((e) => e.id === moved.id));
  assert.deepEqual([...missingPasteIds("[Pasted #42 +3 lines]")], [42], "a real loss stays lost");
});
