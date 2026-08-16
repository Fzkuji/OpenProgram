import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  matchesProgramSearch,
  programsForSelection,
  toolsForSelection,
} from "../components/functions/program-source-categories.ts";

const root = new URL("../", import.meta.url);
const page = readFileSync(new URL("components/functions/functions-page.tsx", root), "utf8");

const programs = [
  { name: "analysis", category: "agentic", description: "Review code" },
  { name: "legacy", category: "unknown", description: "Older agentic entry" },
  { name: "research_agent", category: "app", description: "Research application" },
];
const tools = [
  { name: "read", description: "Read files" },
  { name: "web_search", description: "Search the web" },
];

assert.deepEqual(programsForSelection("__functions__", programs, []), []);
assert.deepEqual(
  programsForSelection("__agentic_functions__", programs, []).map((p) => p.name),
  ["analysis", "legacy"],
);
assert.deepEqual(
  programsForSelection("__applications__", programs, []).map((p) => p.name),
  ["research_agent"],
);
assert.deepEqual(
  programsForSelection("__favorites__", programs, ["analysis"]).map((p) => p.name),
  ["analysis"],
);
assert.deepEqual(toolsForSelection("__functions__", tools, []), tools);
assert.deepEqual(toolsForSelection("__agentic_functions__", tools, []), []);
assert.deepEqual(
  toolsForSelection("__favorites__", tools, ["web_search"]).map((tool) => tool.name),
  ["web_search"],
);
assert.equal(matchesProgramSearch(tools[1], "SEARCH THE"), true);

const categories = page.slice(
  page.indexOf("const sourceCategories = ["),
  page.indexOf("];", page.indexOf("const sourceCategories = [")),
);
assert.match(
  categories,
  /__functions__[\s\S]*Functions[\s\S]*__agentic_functions__[\s\S]*Agentic Functions[\s\S]*__applications__[\s\S]*Applications[\s\S]*__favorites__[\s\S]*Favorites/,
);
assert.doesNotMatch(page, /__all__|All Programs|__uncategorized__|Uncategorized/);
assert.doesNotMatch(page, /api\/tool-profiles|userProfiles|New Profile|profileSelection/);

console.log("program source category checks passed");
