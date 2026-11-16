import assert from "node:assert/strict";
import test from "node:test";

import { addressDisplayParts, shortcutHint } from "../../lib/browser/browser-layout.ts";

test("a resting address shows the host apart from the path, without the scheme", () => {
  assert.deepEqual(addressDisplayParts("https://github.com/Fzkuji/OpenProgram/pulls?q=1#top"), {
    secure: true,
    host: "github.com",
    rest: "/Fzkuji/OpenProgram/pulls?q=1#top",
  });
  assert.deepEqual(addressDisplayParts("http://localhost:18100/"), {
    secure: false,
    host: "localhost:18100",
    rest: "",
  });
});

test("anything that is not a plain web URL is shown as typed", () => {
  assert.equal(addressDisplayParts(""), null);
  assert.equal(addressDisplayParts("openprogram docs"), null);
  assert.equal(addressDisplayParts("file:///Users/me/a.html"), null);
  assert.equal(addressDisplayParts("https://user:pw@example.com/"), null);
});

test("shortcut hints follow the platform", () => {
  assert.equal(shortcutHint("[", { mac: true }), "⌘[");
  assert.equal(shortcutHint("R", { shift: true, mac: true }), "⇧⌘R");
  assert.equal(shortcutHint("R", { mac: false }), "Ctrl+R");
});
