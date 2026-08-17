import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { groupTimelineDays } from "../components/memory/format.ts";

const memoryPage = readFileSync(
  new URL("../components/memory/index.tsx", import.meta.url),
  "utf8",
);
const memoryCss = readFileSync(
  new URL("../components/memory/memory-page.module.css", import.meta.url),
  "utf8",
);
const memoryMarkdown = readFileSync(
  new URL("../components/memory/markdown.ts", import.meta.url),
  "utf8",
);
assert.doesNotMatch(memoryPage, /styles\.writerStatus/);
assert.doesNotMatch(memoryPage, /pending turns/);
assert.doesNotMatch(memoryCss, /\.writerStatus/);
assert.doesNotMatch(memoryPage, /\/api\/memory\/status/);
assert.doesNotMatch(memoryPage, /Commitments|commitments/);
assert.doesNotMatch(memoryCss, /commitment/);
assert.match(memoryPage, /useState<"injected" \| "records">\("injected"\)/);
assert.match(memoryPage, /renderedTokens/);
assert.match(memoryPage, /topics\/core\.md/);
assert.match(memoryPage, /styles\.coreViewSwitch/);
assert.match(memoryCss, /\.coreViewSwitch\s*\{/);
assert.match(memoryPage, /data\.injected_content/);
assert.match(memoryPage, /injectionEnabled/);
assert.match(memoryPage, /const submittedContent = coreEditor\.content/);
assert.match(memoryPage, /fetchCore\(submittedContent\)/);
assert.match(memoryPage, /e\.content === submittedContent/);
assert.match(memoryPage, /coreSaveStatus\(e\.content, submittedContent, r\.ok\)/);
assert.match(memoryMarkdown, /sanitizeHtml\(marked\.parse\(/);
assert.match(memoryPage, /Prompt preview/);
assert.match(memoryPage, /Source records/);
assert.match(memoryPage, /Injection enabled/);
assert.doesNotMatch(memoryPage, />\s*Injected\s*</);
assert.doesNotMatch(memoryPage, />\s*Core records\s*</);
assert.match(memoryPage, /groupTimelineDays\(timelineDays, locale\)/);
assert.match(memoryPage, /timelineGroups\.length === 0/);
assert.match(memoryPage, /<details[^>]*className=\{styles\.timelineYear\}/);
assert.match(memoryPage, /<details[^>]*className=\{styles\.timelineMonth\}/);
assert.doesNotMatch(memoryPage, /formatDate\(day\.mtime, locale\)/);
assert.match(memoryCss, /\.coreTokenMeter\s*\{/);
assert.match(memoryCss, /\.timelineYear\s*\{/);
assert.match(memoryCss, /\.timelineMonth\s*\{/);
assert.match(memoryCss, /\.timelineDay:focus-visible::after,[\s\S]*border:\s*2px solid var\(--accent-blue\)/);

const groupedTimeline = groupTimelineDays([
  { date: "2026-08-07", size: 20, mtime: 2 },
  { date: "2025-12-31", size: 10, mtime: 1 },
  { date: "2026-08-15", size: 30, mtime: 3 },
], "en");
assert.deepEqual(
  groupedTimeline.map((year) => [year.year, year.entryCount, year.months.map((month) => [month.label, month.days.map((day) => day.dayLabel)])]),
  [["2026", 2, [["August", ["15", "07"]]]], ["2025", 1, [["December", ["31"]]]]],
);
assert.deepEqual(groupTimelineDays([
  { date: "2026-13-01", size: 1, mtime: 1 },
  { date: "2026-02-31", size: 1, mtime: 1 },
  { date: "not-a-date", size: 1, mtime: 1 },
]), []);
const partialTimeline = groupTimelineDays([
  { date: "2026", size: 1, mtime: 1 },
  { date: "2026-08", size: 1, mtime: 1 },
  { date: "0099-01-01", size: 1, mtime: 1 },
], "en");
assert.deepEqual(
  partialTimeline.map((year) => [year.year, year.entryCount, year.entries.map((entry) => entry.label), year.months.map((month) => [month.label, month.entries.map((entry) => entry.label), month.days.map((day) => day.dayLabel)])]),
  [["2026", 2, ["Year overview"], [["August", ["Month overview"], []]]], ["0099", 1, [], [["January", [], ["01"]]]]],
);

console.log("check-memory-status: ok");
