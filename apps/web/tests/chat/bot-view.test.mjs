// A conversation with an always-on Agent draws only what was said. Rows
// carry the rowKind the server names (tests/component/webui/chat/test_transcript_reads.py
// holds the report fields a read sends).
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../_support/load.mjs";

installLoader({});
const { botCitations, botFailure, botReplyFiles, botReplyText, botShows, botTimeLine, botWorksAfterText, botWritten, breakLabel, splitBubbles, timeBreaks, MAX_BUBBLES } = await import("../../lib/chat/transcript/bot-view.ts");

test("a report opens with the time and says why it came", () => {
  const text = (english) => english;
  const report = (fields) => ({ id: "r", role: "assistant", rowKind: "assistant", content: "x", reportId: "1", reportTitle: "Stock", ...fields });
  const clock = breakLabel(1000, new Date(1000 * 1000), "en");
  const line = (fields) => botTimeLine(report(fields), 1000, new Date(1000 * 1000), "en", text);
  assert.equal(line({ reportSource: "check_in", reportKind: "finding" }), `${clock} · Noticed while checking in`);
  assert.equal(line({ reportSource: "check_in", reportKind: "done" }), `${clock} · Finished in the background`);
  assert.equal(line({ reportSource: "check_in", reportKind: "approval" }), `${clock} · Asks for your decision`);
  assert.equal(line({ reportKind: "approval" }), `${clock} · Asks for your decision`);
  assert.equal(line({ reportSource: "task", reportKind: "finding" }), `${clock} · Scheduled: Stock`);
  assert.equal(line({ reportKind: "finding" }), clock);
  // A report saved before these fields keeps the time alone.
  assert.equal(line({ reportTitle: undefined, reportKind: "approval" }), clock);
  assert.equal(botTimeLine({ id: "r", role: "assistant", rowKind: "assistant", content: "x" }, 1000, new Date(1000 * 1000), "en", text), clock);
});

test("a report breaks the time run even right after another row", () => {
  const rows = [
    { id: "a", role: "user", rowKind: "user", content: "hi", timestamp: 1000 },
    { id: "b", role: "assistant", rowKind: "assistant", content: "yo", status: "done", timestamp: 1010 },
    { id: "r", role: "assistant", rowKind: "assistant", content: "news", status: "done", timestamp: 1020, reportId: "1" },
  ];
  assert.deepEqual([...timeBreaks(rows).keys()], ["a", "r"]);
});

test("a scheduled run that only left its mark draws nothing and holds no time", () => {
  const reply = (content, fields = {}) => ({ id: content, role: "assistant", rowKind: "assistant", content, status: "done", timestamp: 5000, ...fields });
  for (const mark of ["[nothing new]", "Nothing new.", "【无新内容】", "没有新内容"]) assert.equal(botShows(reply(mark)), false, mark);
  assert.equal(botShows(reply("[nothing new] but the price moved")), true);
  assert.equal(botShows(reply("[could not check] page gone")), false);
  assert.equal(botShows(reply("I could not check the page")), true);
  assert.deepEqual([...timeBreaks([reply("[nothing new]")]).keys()], []);
});

test("a reply is one bubble per paragraph", () => {
  assert.deepEqual(splitBubbles("one\n\ntwo"), ["one", "two"]);
});

test("code, lists and tables stay with the paragraph before them", () => {
  const code = "```\na\n\nb\n```";
  assert.deepEqual(splitBubbles(`run this:\n\n${code}\n\ndone`), [`run this:\n\n${code}\n\ndone`]);
  assert.deepEqual(splitBubbles("items:\n\n- a\n- b"), ["items:\n\n- a\n- b"]);
  assert.deepEqual(splitBubbles("# Title\n\nbody"), ["# Title\n\nbody"]);
});

test("a long reply stops at the cap and the last bubble takes the rest", () => {
  const long = Array.from({ length: MAX_BUBBLES + 2 }, (_, i) => `p${i}`);
  assert.deepEqual(splitBubbles(long.join("\n\n")), ["p0", "p1", "p2", long.slice(3).join("\n\n")]);
});

test("nothing follows a bubble that is already long, and CJK counts double", () => {
  assert.equal(splitBubbles(`${"a".repeat(321)}\n\nb`).length, 1);
  assert.equal(splitBubbles(`${"a".repeat(320)}\n\nb`).length, 2);
  assert.equal(splitBubbles(`${"中".repeat(161)}\n\nb`).length, 1);
});

const FIXTURES = {
  "formula with blank lines": "before\n\n$$\na=b\n\nc=d\n$$\n\nafter",
  "reference definition": "See [doc][1].\n\nMore.\n\n[1]: https://example.com",
  "thematic break": "a\n\n---\n\nb",
  "spaced thematic break": "a\n\n_ _ _\n\nb",
  "pipe-less table": "Intro.\n\nName | Age\n--- | ---\nAnn | 3\n\nAfter.",
  "piped table": "Intro.\n\n| Name | Age |\n| --- | --- |\n| Ann | 3 |\n\nAfter.",
  "dotted list": "Intro.\n\n1. first\n2. second",
  "two-digit list": "Intro.\n\n12) twelve\n\nnext",
  "bullets": "Intro.\n\n• a\n\n• b",
  "CJK numbering": "说明。\n\n一、甲\n\n二、乙",
  "cut fence": "Intro.\n\n```python\nprint(1)\n\nprint(2)",
  "nested fence": "Intro.\n\n````\n```\n\n```\n````\n\ndone",
  "number that is not a list": "Intro.\n\n1.5 is the answer.\n\nnext",
  "bold that is not a rule": "Intro.\n\n***bold***\n\nnext",
  "plain": "one\n\ntwo\n\nthree\n\nfour\n\nfive",
  "bracket formula with blank lines": "before\n\n\\[\na=b\n\nc=d\n\\]\n\nafter",
  "tabbed thematic break": "a\n\n-\t-\t-\n\nb",
  "tabbed list": "Intro.\n\n-\tfirst\n\nnext",
  "indented fence": "Intro.\n\n  ```\n  x\n\n  y\n  ```\n\ndone",
  "indented list": "Intro.\n\n  - a\n  - b",
  "indented code": "Intro.\n\n    a\n\n    b\n\ndone",
  "inline triple backticks": "```x``` is short\n\nnext\n\nmore",
};

for (const [name, text] of Object.entries(FIXTURES)) {
  test(`while streaming, bubbles only grow: ${name}`, () => {
    const final = splitBubbles(text, { streaming: true });
    let previous = 0;
    for (let end = 1; end <= text.length; end += 1) {
      const bubbles = splitBubbles(text.slice(0, end), { streaming: true });
      assert.ok(bubbles.length >= previous, `count dropped at ${JSON.stringify(text.slice(0, end))}`);
      previous = bubbles.length;
      bubbles.slice(0, -1).forEach((bubble, index) => assert.ok(final[index].startsWith(bubble), `bubble ${index} changed at ${end}`));
    }
  });
}

test("each construct lands in the right bubble when settled", () => {
  const settled = (name) => splitBubbles(FIXTURES[name]);
  assert.deepEqual(settled("formula with blank lines"), ["before", "$$\na=b\n\nc=d\n$$\n\nafter"]);
  assert.deepEqual(settled("reference definition"), [FIXTURES["reference definition"]]);
  assert.deepEqual(settled("thematic break"), [FIXTURES["thematic break"]]);
  assert.deepEqual(settled("pipe-less table"), [FIXTURES["pipe-less table"]]);
  assert.deepEqual(settled("piped table"), [FIXTURES["piped table"]]);
  assert.deepEqual(settled("dotted list"), [FIXTURES["dotted list"]]);
  assert.deepEqual(settled("two-digit list"), [FIXTURES["two-digit list"]]);
  assert.deepEqual(settled("bullets"), [FIXTURES.bullets]);
  assert.deepEqual(settled("CJK numbering"), [FIXTURES["CJK numbering"]]);
  assert.deepEqual(settled("number that is not a list"), ["Intro.", "1.5 is the answer.", "next"]);
  assert.deepEqual(settled("bracket formula with blank lines"), ["before", "\\[\na=b\n\nc=d\n\\]\n\nafter"]);
  assert.deepEqual(settled("tabbed thematic break"), [FIXTURES["tabbed thematic break"]]);
  assert.deepEqual(settled("tabbed list"), [FIXTURES["tabbed list"]]);
  assert.deepEqual(settled("indented fence"), [FIXTURES["indented fence"]]);
  assert.deepEqual(settled("indented list"), [FIXTURES["indented list"]]);
  assert.deepEqual(settled("indented code"), [FIXTURES["indented code"]]);
  assert.deepEqual(settled("inline triple backticks"), ["```x``` is short", "next", "more"]);
});

test("short paragraphs keep their bubbles, then a table takes its intro and the rest", () => {
  assert.deepEqual(splitBubbles("One.\n\nTwo.\n\nHere:\n\n| a |\n| - |\n| 1 |\n\nEnd."), ["One.", "Two.", "Here:\n\n| a |\n| - |\n| 1 |\n\nEnd."]);
});

test("a marker that is not classified yet waits off screen", () => {
  for (const held of ["-", "1", "1.", "12)", "•", "一、", "--", "|", "| a | b |", "``"]) {
    assert.deepEqual(splitBubbles(`Intro.\n\n${held}`, { streaming: true }), ["Intro."], held);
    assert.equal(splitBubbles(`Intro.\n\n${held}`).join("\n\n"), `Intro.\n\n${held}`, held);
  }
});

test("the first piece has nothing to join, so it never waits and the row is never empty", () => {
  for (const first of ["-", "1", "1.", "|", "| a | b |", "``"]) {
    assert.deepEqual(splitBubbles(first, { streaming: true }), [first]);
  }
});

test("a lone bold line is not followed by a new bubble", () => {
  assert.deepEqual(splitBubbles("**Title**\n\nbody"), ["**Title**\n\nbody"]);
});

test("only what was said is shown", () => {
  assert.equal(botShows({ id: "u", role: "user", rowKind: "user", rowKind: "user", content: "hi" }), true);
  assert.equal(botShows({ id: "n", role: "user", rowKind: "user", rowKind: "agent_notice", content: "", source: "job_followup" }), false);
  assert.equal(botShows({ id: "r", role: "user", rowKind: "user", rowKind: "silent", content: "" }), false);
  assert.equal(botShows({ id: "s", role: "system", rowKind: "system_text", content: "x" }), false);
  assert.equal(botShows({ id: "a", role: "assistant", rowKind: "assistant", rowKind: "assistant", content: "", blocks: [{ type: "tool", tool: "bash" }] }), false);
  assert.equal(botShows({ id: "e", role: "assistant", rowKind: "assistant", rowKind: "assistant", content: "", status: "error" }), true);
});

test("the reply text leaves its steps out", () => {
  const msg = { id: "a", role: "assistant", rowKind: "assistant", content: "x", blocks: [
    { type: "thinking", text: "hmm" }, { type: "text", text: "first" }, { type: "tool", tool: "bash" }, { type: "text", text: "second" },
  ] };
  assert.equal(botReplyText(msg), "first\n\nsecond");
});

test("a block cut inside code does not swallow the text after it", () => {
  const msg = { id: "a", role: "assistant", rowKind: "assistant", content: "", blocks: [
    { type: "text", text: "```py\nx" }, { type: "tool", tool: "bash" }, { type: "text", text: "after" },
  ] };
  assert.equal(botReplyText(msg), "```py\nx\n```\n\nafter");
  const last = { id: "b", role: "assistant", rowKind: "assistant", content: "", blocks: [{ type: "text", text: "```py\nx" }] };
  assert.equal(botReplyText(last), "```py\nx");
});

test("the time comes back after a pause, measured over rows that are shown", () => {
  const rows = [
    { id: "a", role: "user", rowKind: "user", rowKind: "user", content: "x", timestamp: 1000 },
    { id: "b", role: "assistant", rowKind: "assistant", rowKind: "assistant", content: "y", timestamp: 1100 },
    { id: "hidden", role: "assistant", rowKind: "assistant", rowKind: "assistant", content: "", timestamp: 1200 },
    { id: "c", role: "user", rowKind: "user", rowKind: "user", content: "z", timestamp: 1100 + 1800 },
  ];
  assert.deepEqual([...timeBreaks(rows).keys()], ["a", "c"]);
});

test("today's time is a clock, older ones carry the day", () => {
  const now = new Date(2026, 10, 8, 20, 0);
  const at = (date) => date.getTime() / 1000;
  assert.doesNotMatch(breakLabel(at(new Date(2026, 10, 8, 9, 5)), now, "en-GB"), /[A-Za-z]/);
  assert.match(breakLabel(at(new Date(2026, 10, 3, 9, 5)), now, "en-GB"), /[A-Za-z]/);
});

const { playsBotSwitch } = await import("../../components/chat/messages/list/use-bot-view-transition.ts");

test("only a change in a conversation already on screen plays the switch", () => {
  const settled = { sameSession: true, agentsLoaded: true, conversationKnown: true, msSinceOpened: 5000, reducedMotion: false };
  assert.equal(playsBotSwitch(settled), true);
  assert.equal(playsBotSwitch({ ...settled, sameSession: false }), false);
  assert.equal(playsBotSwitch({ ...settled, agentsLoaded: false }), false);
  assert.equal(playsBotSwitch({ ...settled, conversationKnown: false }), false);
  assert.equal(playsBotSwitch({ ...settled, msSinceOpened: 100 }), false);
  assert.equal(playsBotSwitch({ ...settled, reducedMotion: true }), false);
});

test("a file sent with the reply shows though only the stored content carries its marker", () => {
  const parse = (content) => ({ attachments: [...content.matchAll(/\[file (\S+)\]/g)].map((match) => ({ path: match[1] })) });
  const msg = { id: "a", role: "assistant", rowKind: "assistant", content: "hello [file /tmp/a.png]", blocks: [{ type: "text", text: "hello" }] };
  assert.deepEqual(botReplyFiles(msg, parse), [{ path: "/tmp/a.png" }]);
});

test("a marker in both the text and the content is one file", () => {
  const parse = (content) => ({ attachments: [...content.matchAll(/\[file (\S+)\]/g)].map((match) => ({ path: match[1] })) });
  const msg = { id: "a", role: "assistant", rowKind: "assistant", content: "[file /tmp/a.png]", blocks: [{ type: "text", text: "[file /tmp/a.png]" }] };
  assert.deepEqual(botReplyFiles(msg, parse), [{ path: "/tmp/a.png" }]);
});

test("a failed reply draws no saved error as its text, but one with detail keeps what it wrote", () => {
  assert.equal(botReplyText({ role: "assistant", rowKind: "assistant", status: "error", content: "HTTP 401" }), "");
  assert.equal(botReplyText({ role: "assistant", rowKind: "assistant", status: "error", content: "half", errorDetail: "HTTP 500" }), "half");
  assert.equal(botShows({ role: "assistant", rowKind: "assistant", status: "error", content: "HTTP 401" }), true);
});

test("an interrupted reply shows until a restart carries it on, and a stopped one says nothing", () => {
  assert.equal(botFailure({ status: "error" }), "error");
  assert.equal(botFailure({ status: "interrupted" }), "interrupted");
  assert.equal(botFailure({ status: "interrupted", continuedBy: "next" }), null);
  assert.equal(botFailure({ status: "cancelled" }), null);
  assert.equal(botShows({ role: "assistant", rowKind: "assistant", status: "interrupted", blocks: [] }), true);
  assert.equal(botShows({ role: "assistant", rowKind: "assistant", status: "cancelled", blocks: [] }), false);
});

test("the restart prompt is not something a person said", () => {
  assert.equal(botShows({ role: "user", rowKind: "user", content: "Continue", trigger: "restart" }), false);
});

test("the dots come back only when the last block is not text", () => {
  assert.equal(botWorksAfterText({ blocks: [{ type: "text", text: "a" }] }), false);
  assert.equal(botWorksAfterText({ blocks: [{ type: "text", text: "a" }, { type: "tool", tool: "x" }] }), true);
  assert.equal(botWorksAfterText({ content: "a" }), false);
});

test("citations of every text block reach the one row under the reply", () => {
  const first = { url: "https://a.example" };
  const second = { url: "https://b.example" };
  const msg = { blocks: [{ type: "text", text: "a", citations: [first] }, { type: "tool", tool: "x" }, { type: "text", text: "b", citations: [second] }] };
  assert.deepEqual(botCitations(msg), [first, second]);
});

test("the marker a restart leaves is not something the Agent wrote, alone or joined", async () => {
  const { joinedReply } = await import("../../lib/chat/transcript/restart-continuation.ts");
  const marker = "[interrupted] worker restarted mid-turn";
  const stamped = { id: "cut", role: "assistant", rowKind: "assistant", status: "interrupted", content: marker, blocks: [{ type: "tool", tool: "bash" }] };
  assert.equal(botReplyText(stamped), "");
  assert.equal(botFailure(stamped), "interrupted");
  assert.equal(botShows(stamped), true);
  const next = { id: "next", role: "assistant", rowKind: "assistant", status: "done", content: "carried on", blocks: [{ type: "text", text: "carried on" }] };
  assert.equal(botReplyText(joinedReply([botWritten(stamped)], botWritten(next), (content) => content)), "carried on");
  // Without the strip, joining turns the marker into a text block.
  assert.match(botReplyText(joinedReply([stamped], next, (content) => content)), /^\[interrupted\]/);
});

test("a continuation that failed does not draw its saved error as text", async () => {
  const { joinedReply } = await import("../../lib/chat/transcript/restart-continuation.ts");
  const cut = { id: "cut", role: "assistant", rowKind: "assistant", status: "interrupted", blocks: [{ type: "text", text: "half" }], content: "half" };
  const failed = { id: "next", role: "assistant", rowKind: "assistant", status: "error", content: "HTTP 401" };
  const joined = joinedReply([botWritten(cut)], botWritten(failed), (content) => content);
  assert.equal(botReplyText(joined), "half");
  assert.equal(botFailure(joined), "error");
});

test("a new day brings the time back within the pause", () => {
  const late = new Date(2026, 10, 8, 23, 50).getTime();
  const early = new Date(2026, 10, 9, 0, 5).getTime();
  const rows = [{ id: "a", role: "user", rowKind: "user", content: "x", timestamp: late }, { id: "b", role: "user", rowKind: "user", content: "y", timestamp: early }];
  assert.deepEqual([...timeBreaks(rows).keys()], ["a", "b"]);
});

test("a restart's cut-off reply gives its time to the reply carrying it on, written or not", () => {
  const at = new Date(2026, 10, 9, 12).getTime();
  const rows = [
    { id: "cut", role: "assistant", rowKind: "assistant", status: "interrupted", content: "half", timestamp: at, continuedBy: "next" },
    { id: "next", role: "assistant", rowKind: "assistant", status: "streaming", blocks: [], timestamp: at + 1000 },
  ];
  assert.deepEqual([...timeBreaks(rows).keys()], ["next"]);
});

test("a reply still writing holds its time, since the lines are worked out when the row is added", () => {
  const at = new Date(2026, 10, 9, 9).getTime();
  const earlier = new Date(2026, 10, 8, 22).getTime();
  const rows = [
    { id: "said", role: "user", rowKind: "user", content: "x", timestamp: earlier },
    { id: "prompt", role: "user", rowKind: "user", content: "run", source: "scheduler", timestamp: at },
    { id: "reply", role: "assistant", rowKind: "assistant", status: "streaming", blocks: [], timestamp: at + 1000 },
  ];
  assert.deepEqual([...timeBreaks(rows).keys()], ["said", "reply"]);
  assert.deepEqual([...timeBreaks([rows[0], { ...rows[2], display: "runtime", rowKind: "function_run" }]).keys()], ["said"]);
  assert.deepEqual([...timeBreaks([rows[0], { ...rows[2], function: "run" }]).keys()], ["said"]);
  assert.deepEqual([...timeBreaks([rows[0], { ...rows[2], status: "done" }]).keys()], ["said"]);
});

test("a time line is not spent on a restart that left nothing to draw", () => {
  const at = new Date(2026, 10, 9, 12).getTime();
  const silent = [
    { id: "cut", role: "assistant", rowKind: "assistant", status: "interrupted", content: "[interrupted] worker restarted mid-turn", timestamp: at, continuedBy: "next" },
    { id: "next", role: "assistant", rowKind: "assistant", status: "done", blocks: [{ type: "tool", tool: "x" }], timestamp: at + 1000 },
  ];
  assert.deepEqual([...timeBreaks(silent).keys()], []);
  const chain = [
    { id: "first", role: "assistant", rowKind: "assistant", status: "interrupted", content: "half", timestamp: at, continuedBy: "second" },
    { id: "second", role: "assistant", rowKind: "assistant", status: "interrupted", content: "", timestamp: at + 1000, continuedBy: "third" },
    { id: "third", role: "assistant", rowKind: "assistant", status: "done", blocks: [{ type: "tool", tool: "x" }], timestamp: at + 2000 },
  ];
  assert.deepEqual([...timeBreaks(chain).keys()], ["third"]);
});

test("a joined reply reads as one and a cut code block does not swallow the rest", async () => {
  const { joinedReply } = await import("../../lib/chat/transcript/restart-continuation.ts");
  const cut = { id: "cut", role: "assistant", rowKind: "assistant", blocks: [{ type: "text", text: "```\ncode" }], status: "interrupted" };
  const next = { id: "next", role: "assistant", rowKind: "assistant", blocks: [{ type: "text", text: "carried on" }], status: "done" };
  const text = botReplyText(joinedReply([cut], next, (content) => content));
  assert.match(text, /^```\ncode\n```\n\ncarried on$/);
});
