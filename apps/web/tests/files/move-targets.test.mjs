import assert from "node:assert/strict";
import test from "node:test";

import { canMoveInto, matchFolders, recentMoveTargets, rememberMoveTarget, splitFolderInput, typedFolder } from "../../components/files/explorer/move-targets.ts";

test("a typed folder path is cleaned and stays inside the project", () => {
  assert.equal(typedFolder("  docs/notes/ "), "docs/notes");
  assert.equal(typedFolder("/docs//notes"), "docs/notes");
  assert.equal(typedFolder("docs\\notes"), "docs/notes");
  assert.equal(typedFolder(""), null);
  assert.equal(typedFolder("../outside"), null);
  assert.equal(typedFolder("docs/./notes"), null);
});

test("a folder never takes itself, its own inside, or the place things already are", () => {
  assert.equal(canMoveInto(["src/a.txt"], "docs"), true);
  assert.equal(canMoveInto(["src/a.txt"], ""), true);
  assert.equal(canMoveInto(["src/a.txt"], "src"), false, "already there");
  assert.equal(canMoveInto(["src/a.txt", "lib/b.txt"], "src"), true, "only some are there");
  assert.equal(canMoveInto(["src"], "src"), false);
  assert.equal(canMoveInto(["src"], "src/deep"), false);
  assert.equal(canMoveInto(["src"], "srcs"), true);
});

test("recent targets are newest first, unique and short, per project", () => {
  for (const dir of ["a", "b", "c", "a", "d", "e", "f"]) rememberMoveTarget("p1", dir);
  assert.deepEqual(recentMoveTargets("p1"), ["f", "e", "d", "a", "c"]);
  assert.deepEqual(recentMoveTargets("p2"), []);
});

test("the input reads as a path: a folder to list and a name inside it", () => {
  assert.deepEqual(splitFolderInput("pro"), { base: "", term: "pro" });
  assert.deepEqual(splitFolderInput("Desktop/pro"), { base: "Desktop", term: "pro" });
  assert.deepEqual(splitFolderInput("/Desktop/"), { base: "Desktop", term: "" });
  assert.deepEqual(splitFolderInput("a\\b/c"), { base: "a/b", term: "c" });
  assert.deepEqual(splitFolderInput("../x"), { base: null, term: "x" });
});

test("folder names that start with the term come before ones that only hold it", () => {
  assert.deepEqual(matchFolders(["my-docs", "Docs", "src", "docs2"], "doc"), ["Docs", "docs2", "my-docs"]);
  assert.deepEqual(matchFolders(["a", "b"], ""), ["a", "b"]);
});
