/**
 * message-rail packing round-trip.
 *
 * The rail subscribes to the session store through `useShallow`, which
 * only compares one level deep — so each row is flattened to a single
 * string and parsed back on the way out. Message text and file paths can
 * contain separators, spaces, tabs, newlines, quotes, and Unicode, so the
 * row uses a JSON tuple. This asserts that round-trip and that the whole-map
 * subscription the packing exists to avoid has not crept back in.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  packRailRow,
  unpackRailRow,
} from "../components/chat/messages/message-rail-row.ts";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(
  join(here, "..", "components", "chat", "messages", "message-rail.tsx"),
  "utf8",
);

// The rail must not subscribe to the whole messagesById map: that object
// is rebuilt on every streaming token, so it would re-render (and
// re-derive, O(n^2)) the rail per delta.
if (/useSessionStore\(\(s\) => s\.messagesById\)/.test(src)) {
  throw new Error(
    "message-rail subscribes to the whole messagesById map — that "
    + "re-renders the rail on every streaming token",
  );
}
if (!src.includes("useShallow")) {
  throw new Error("message-rail lost its useShallow subscription");
}
if (!src.includes("hiddenKey") || !src.includes("hidden.has(m.id)")) {
  throw new Error("message-rail must omit folded covered originals from ticks");
}
if (!src.includes("querySelector(`[data-msg-id=")) {
  throw new Error("message-rail must probe real [data-msg-id] nodes");
}
if (!src.includes(".compaction-orig-fold[data-open='0']")) {
  throw new Error("rail click must ignore folded covered originals");
}
if (/react-virtuoso|react-window|@tanstack\/react-virtual/.test(src)) {
  throw new Error("message-rail must not depend on a virtual list");
}

const cases = [
  {
    id: "m1",
    content: "hello world  with   spaces",
    preview: "hello world with spaces",
    assistantId: "a1",
    assistantSummary: "sure thing",
    assistantTurnFiles: undefined,
    assistantFileWriteState: "attempted",
    assistantReverted: false,
  },
  // Newlines, tabs, quotes and markdown fences all have to survive.
  {
    id: "m2",
    content: "line one\nline two\ttabbed\n\n```js\nconst a = \"b\";\n```",
    preview: "line one line two tabbed",
    assistantId: undefined,
    assistantSummary: undefined,
    assistantTurnFiles: undefined,
    assistantFileWriteState: "none",
    assistantReverted: false,
  },
  // Empty optional fields must come back as undefined, not "".
  {
    id: "m3",
    content: "x",
    preview: "x",
    assistantId: undefined,
    assistantSummary: undefined,
    assistantTurnFiles: undefined,
    assistantFileWriteState: "failed",
    assistantReverted: false,
  },
  // CJK + emoji payload.
  {
    id: "m4",
    content: "中文内容，带标点。🎯",
    preview: "中文内容，带标点。🎯",
    assistantId: "a4",
    assistantSummary: "回复摘要",
    assistantTurnFiles: {
      version: 2,
      files: [{ path: "src/a␟b\n\"文.ts", op: "modify", added: 1, removed: 0 }],
      file_count: 1,
      added: 1,
      removed: 0,
    },
    assistantFileWriteState: "none",
    assistantReverted: true,
  },
];

for (const want of cases) {
  const got = unpackRailRow(packRailRow(want));
  for (const key of ["id", "content", "preview", "assistantId", "assistantSummary", "assistantFileWriteState", "assistantReverted"]) {
    if (got[key] !== want[key]) {
      throw new Error(
        `round-trip lost ${key} for ${want.id}: `
        + `${JSON.stringify(got[key])} !== ${JSON.stringify(want[key])}`,
      );
    }
  }
  if (JSON.stringify(got.assistantTurnFiles) !== JSON.stringify(want.assistantTurnFiles)) {
    throw new Error(`round-trip lost assistantTurnFiles for ${want.id}`);
  }
}

const shouldRenderTurnFiles = (summary, writeState) =>
  Boolean(summary) || writeState !== "none";
if (!shouldRenderTurnFiles(undefined, "attempted")) {
  throw new Error("legacy attempted writes must mount the file-change surface");
}
if (!shouldRenderTurnFiles(undefined, "failed")) {
  throw new Error("failed writes must retain their failure note");
}
if (shouldRenderTurnFiles(undefined, "none")) {
  throw new Error("plain replies must not mount the file-change surface");
}
if (!src.includes("shouldRenderTurnFiles(") || !src.includes("writeState={assistantFileWriteState}")) {
  throw new Error("message rail must pass its file-write state into TurnFilesChips");
}

const legacy = unpackRailRow(["old", "text", "preview", "assistant", "reply"].join("␟"));
if (legacy.id !== "old" || legacy.assistantId !== "assistant"
  || legacy.assistantFileWriteState !== "none") {
  throw new Error("legacy five-field rows must remain readable");
}

// A row count change must be visible to the shallow compare, and an
// unchanged transcript must produce identical strings (that equality is
// exactly what makes streaming deltas free).
const a = cases.map(packRailRow);
const b = cases.map(packRailRow);
if (a.length !== b.length || a.some((v, i) => v !== b[i])) {
  throw new Error("packRow is not deterministic — the shallow compare would thrash");
}

console.log(`check-message-rail: ok (${cases.length} round-trips)`);
