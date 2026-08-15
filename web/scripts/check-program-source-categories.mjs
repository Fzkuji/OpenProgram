import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  matchesProgramSearch,
  profileSelection,
  programsForSelection,
  selectionProfileName,
  toolsForSelection,
} from "../components/functions/program-source-categories.ts";

const root = new URL("../", import.meta.url);
const page = readFileSync(new URL("components/functions/functions-page.tsx", root), "utf8");
const card = readFileSync(new URL("components/functions/function-card.tsx", root), "utf8");

const programs = [
  { name: "analysis", category: "agentic", description: "Review code" },
  { name: "legacy", category: "unknown", description: "Older agentic entry" },
  { name: "research_agent", category: "app", description: "Research application" },
];
const tools = [
  { name: "read", description: "Read files" },
  { name: "web_search", description: "Search the web" },
];
const profiles = { research: ["research_agent", "web_search"] };
const collidingProfiles = {
  __functions__: ["research_agent"],
  __agentic_functions__: ["read"],
  __applications__: ["analysis"],
};

assert.deepEqual(programsForSelection("__all__", programs, [], profiles), programs);
assert.deepEqual(programsForSelection("__functions__", programs, [], profiles), []);
assert.deepEqual(
  programsForSelection("__agentic_functions__", programs, [], profiles).map((p) => p.name),
  ["analysis", "legacy"],
);
assert.deepEqual(
  programsForSelection("__applications__", programs, [], profiles).map((p) => p.name),
  ["research_agent"],
);
assert.deepEqual(
  programsForSelection("__favorites__", programs, ["analysis"], profiles).map((p) => p.name),
  ["analysis"],
);
assert.deepEqual(
  programsForSelection("__uncategorized__", programs, [], profiles).map((p) => p.name),
  ["analysis", "legacy"],
);
assert.deepEqual(
  programsForSelection(profileSelection("research"), programs, [], profiles).map((p) => p.name),
  ["research_agent"],
);
assert.deepEqual(toolsForSelection("__all__", tools, profiles), tools);
assert.deepEqual(toolsForSelection("__functions__", tools, profiles), tools);
assert.deepEqual(toolsForSelection("__agentic_functions__", tools, profiles), []);
assert.deepEqual(toolsForSelection("__applications__", tools, profiles), []);
assert.deepEqual(toolsForSelection("__favorites__", tools, profiles), []);
assert.deepEqual(
  toolsForSelection("__uncategorized__", tools, profiles).map((tool) => tool.name),
  ["read"],
);
assert.deepEqual(
  toolsForSelection(profileSelection("research"), tools, profiles).map((tool) => tool.name),
  ["web_search"],
);
assert.equal(matchesProgramSearch(tools[1], "SEARCH THE"), true);
assert.equal(matchesProgramSearch(tools[0], "missing"), false);
for (const name of Object.keys(collidingProfiles)) {
  const selection = profileSelection(name);
  assert.equal(selectionProfileName(selection), name);
  assert.deepEqual(
    programsForSelection(selection, programs, [], collidingProfiles).map((p) => p.name),
    collidingProfiles[name].filter((item) => programs.some((program) => program.name === item)),
  );
  assert.deepEqual(
    toolsForSelection(selection, tools, collidingProfiles).map((tool) => tool.name),
    collidingProfiles[name].filter((item) => tools.some((tool) => tool.name === item)),
  );
}

const sourceFolderDefinition = page.slice(
  page.indexOf("const sourceFolders = ["),
  page.indexOf("const userProfiles", page.indexOf("const sourceFolders = [")),
);
assert.match(
  sourceFolderDefinition,
  /id: "__all__"[\s\S]*text\("All Programs", "全部程序"\)[\s\S]*id: "__functions__"[\s\S]*text\("Functions", "函数"\)[\s\S]*id: "__agentic_functions__"[\s\S]*text\("Agentic Functions", "Agentic 函数"\)[\s\S]*id: "__applications__"[\s\S]*text\("Applications", "应用"\)[\s\S]*id: "__favorites__"[\s\S]*text\("Favorites", "收藏"\)[\s\S]*id: "__uncategorized__"[\s\S]*text\("Uncategorized", "未分类"\)/,
  "visible fixed rows must preserve the source id, label, and order contract",
);
assert.match(
  page,
  /count:\s*functions\.length\s*\+\s*tools\.length/,
  "All Programs must count both endpoint result sets",
);
assert.match(
  page,
  /new Set\(\[\.\.\.functions\.map[\s\S]*\.\.\.tools\.map/,
  "profile counts must include both regular and agentic entries",
);
const fixedRows = page.slice(
  page.indexOf("{sourceFolders.map"),
  page.indexOf("<div className={styles.profileSep}", page.indexOf("{sourceFolders.map")),
);
assert.match(
  fixedRows,
  /sourceFolders\.map[\s\S]*onClick=\{\(\) => setProfile\(f\.id\)\}[\s\S]*\)\)\}/,
  "fixed source rows must be rendered as click filters",
);
assert.doesNotMatch(
  fixedRows,
  /dragOver|onDragOver|onDrop/,
  "fixed source rows must not mutate profile membership through drag and drop",
);
assert.match(
  page,
  /visibleFunctions\.length\s*===\s*0\s*&&\s*visibleTools\.length\s*===\s*0/,
  "empty state must use both catalogs",
);
const profileRows = page.slice(
  page.indexOf("{userProfiles.map"),
  page.indexOf("{creatingProfile &&", page.indexOf("{userProfiles.map")),
);
assert.match(profileRows, /active=\{profile\s*===\s*profileSelection\(name\)\}/);
assert.match(profileRows, /onClick=\{\(\) => setProfile\(profileSelection\(name\)\)\}/);
assert.match(profileRows, /dragOver=\{dragOver\s*===\s*name\}/);
assert.match(profileRows, /onDragOver=\{\(e\) => onFolderDragOver\(e, name\)\}/);
assert.match(profileRows, /onDrop=\{\(e\) => onFolderDrop\(e, name\)\}/);
assert.doesNotMatch(card, /p\.source|cardSource|📁|📦/u);

console.log("program source category checks passed");
