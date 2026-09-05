import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { groupTimelineDays } from "../components/memory/format.ts";

const normalizedText = (path) => readFileSync(path, "utf8").replace(/\r\n?/g, "\n");
const memoryPage = normalizedText(
  new URL("../components/memory/index.tsx", import.meta.url),
);
const memoryCss = normalizedText(
  new URL("../components/memory/memory-page.module.css", import.meta.url),
);
const memoryParts = normalizedText(
  new URL("../components/memory/parts.tsx", import.meta.url),
);
const memoryMarkdown = normalizedText(
  new URL("../components/memory/markdown.ts", import.meta.url),
);

function cssRules(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const matches = [...memoryCss.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "g"))];
  assert.ok(matches.length, `missing CSS rule: ${selector}`);
  return matches.map((match) => match[1]);
}

function cssRule(selector) {
  return cssRules(selector)[0];
}
assert.doesNotMatch(memoryPage, /styles\.writerStatus/);
assert.doesNotMatch(memoryPage, /pending turns/);
assert.doesNotMatch(memoryCss, /\.writerStatus/);
assert.doesNotMatch(memoryPage, /\/api\/memory\/status/);
assert.doesNotMatch(memoryPage, /Commitments|commitments/);
assert.doesNotMatch(memoryCss, /commitment/);
assert.doesNotMatch(memoryPage, /Memory totals/);
assert.doesNotMatch(memoryPage, /styles\.headerStats/);
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
assert.match(memoryMarkdown, /sanitizeHtml\(renderObsidianMarkdown\(/);
assert.match(memoryMarkdown, /\(source\) => marked\.parse\(source/);
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
assert.match(cssRule(".rightPane"), /padding:\s*14px/);
assert.match(cssRule(".rightPane"), /background:\s*var\(--bg-secondary\)/);
assert.match(cssRule(".editor"), /border:\s*1px solid var\(--border\)/);
assert.match(cssRule(".editor"), /border-radius:\s*12px/);
assert.ok(cssRules(".markdown").some((rule) => /max-width:\s*780px/.test(rule) && /margin:\s*0 auto/.test(rule)));
assert.match(cssRule(".emptyPanel"), /max-width:\s*560px/);
assert.match(cssRule(".emptyPanel"), /border:\s*1px solid var\(--border\)/);
assert.match(cssRule(".emptyPanel"), /border-radius:\s*14px/);
assert.equal((memoryPage.match(/styles\.treeEmpty/g) || []).length, 2);
assert.doesNotMatch(memoryPage, /styles\.contextHeader/);
assert.doesNotMatch(memoryPage, /styles\.coreSidebar/);
assert.match(memoryPage, /styles\.coreControls/);
assert.equal((memoryPage.match(/injectedTokens\.toLocaleString\(\)/g) || []).length, 1);
assert.match(memoryCss, /@media\s*\(max-width:\s*720px\)[\s\S]*\.treeEmpty\s*\{\s*display:\s*none/);
assert.match(memoryCss, /\.treeEmpty\s*\+\s*\.rightPane\s*\{[^}]*grid-row:\s*2\s*\/\s*-1/);
assert.match(memoryPage, /styles\.recentEvent/);
assert.match(memoryPage, /r\.headers\.get\("X-Memory-Recent-Limit"\)/);
assert.match(memoryPage, /recentEvents\.length\} \/ \{recentLimit\}/);
assert.doesNotMatch(memoryPage, /style=\{\{ marginBottom: "1rem" \}\}/);
assert.doesNotMatch(memoryParts, /styles\.editorMeta/);
assert.doesNotMatch(memoryParts, /<div className=\{styles\.editorMeta\}/);
assert.match(memoryParts, /styles\.editorHeaderMeta/);
assert.ok(cssRules(".editorHeader").some((rule) => /min-height:\s*76px/.test(rule)));
assert.match(cssRule(".tabBtn"), /height:\s*36px/);
assert.match(cssRule(".sidebarRow"), /min-height:\s*36px/);
assert.match(cssRule(".sidebarRow"), /padding:\s*0 10px/);
assert.match(cssRule(".sidebarRow"), /border-radius:\s*var\(--ui-list-radius\)/);
assert.match(cssRule(".sidebarRow"), /font-size:\s*13px/);
assert.match(memoryParts, /styles\.folderRow[^\n]*styles\.sidebarRow/);
assert.match(memoryParts, /styles\.fileRow[^\n]*styles\.sidebarRow/);
assert.match(memoryPage, /styles\.timelineYearSummary[^\n]*styles\.sidebarRow/);
assert.match(memoryPage, /styles\.timelineDay[^\n]*styles\.sidebarRow/);
assert.match(memoryParts, /export function DisclosureChevron/);
assert.match(memoryParts, /<path d="M3 2l3 3-3 3"/);
assert.match(memoryParts, /strokeWidth="1\.5" strokeLinecap="round" strokeLinejoin="round"/);
assert.match(memoryParts, /aria-hidden="true" focusable="false"/);
assert.match(memoryParts, /<DisclosureChevron className=\{`\$\{styles\.chevron\}/);
assert.match(memoryParts, /className=\{`\$\{styles\.folderRow\}[^\n]*aria-expanded=\{isExpanded\}/);
assert.match(memoryPage, /DisclosureChevron,/);
assert.equal((memoryPage.match(/<DisclosureChevron className=\{styles\.timelineChevron\}/g) || []).length, 2);
assert.doesNotMatch(memoryCss, /content:\s*["']›["']/);
assert.match(cssRule(".chevronOpen"), /transform:\s*rotate\(90deg\)/);
assert.match(memoryCss, /\.chevron:not\(\.chevronOpen\)\s*\{[^}]*transform:\s*rotate\(0deg\)/);
assert.match(cssRule(".disclosureChevron"), /width:\s*14px/);
assert.match(cssRule(".disclosureChevron"), /height:\s*14px/);
assert.match(memoryParts, /styles\.disclosureChevron/);
assert.equal((memoryPage.match(/styles\.sidebarCount/g) || []).length, 2);
assert.match(memoryParts, /styles\.sidebarCount/);
assert.doesNotMatch(memoryPage, /entryCount\}\s*\{text\(/);
assert.match(cssRule(".timelineChevron"), /transform:\s*rotate\(0deg\)/);
assert.match(cssRule(".timelineYearSummary,\n.timelineMonthSummary"), /list-style:\s*none/);
assert.match(memoryCss, /\.timelineYearSummary::\-webkit-details-marker,[\s\S]*\.timelineMonthSummary::\-webkit-details-marker\s*\{[^}]*display:\s*none/);
assert.match(memoryCss, /\.timelineYear\[open\]\s*>\s*\.timelineYearSummary\s*>\s*\.timelineChevron/);
assert.match(memoryCss, /\.timelineMonth\[open\]\s*>\s*\.timelineMonthSummary\s*>\s*\.timelineChevron\s*\{[^}]*transform:\s*rotate\(90deg\)/);
assert.match(memoryCss, /@media\s*\(max-width:\s*720px\)[\s\S]*\.editorHeader\s*\{[^}]*flex-wrap:\s*wrap/);
assert.match(memoryCss, /@media\s*\(max-width:\s*720px\)[\s\S]*\.editorActions\s*\{[^}]*width:\s*100%[^}]*flex-wrap:\s*wrap/);

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
