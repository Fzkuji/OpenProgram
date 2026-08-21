import assert from "node:assert/strict";
import test from "node:test";

import {
  matchingIndexes,
  visibleSearchPaths,
} from "../components/files/explorer-search.ts";

test("fuzzy and exact matching return only matched character positions", () => {
  assert.deepEqual(matchingIndexes("paper_search", "pa", true), [0, 1]);
  assert.deepEqual(matchingIndexes("templates", "pa", true), [3, 5]);
  assert.equal(matchingIndexes("research_pipeline", "pa", true), null);
  assert.deepEqual(matchingIndexes("paper_search", "paper", false), [0, 1, 2, 3, 4]);
  assert.equal(matchingIndexes("templates", "pa", false), null);
});

test("filter mode retains matching paths and their ancestors", () => {
  const paths = [
    "tools",
    "workflow",
    "workflow/paper_search",
    "workflow/research_pipeline",
    "applications",
  ];
  assert.deepEqual(
    [...visibleSearchPaths(paths, "pa", true)],
    ["workflow", "workflow/paper_search", "applications"],
  );
});
