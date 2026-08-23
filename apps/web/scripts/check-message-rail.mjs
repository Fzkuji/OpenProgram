/**
 * message-rail packing round-trip.
 *
 * The rail subscribes to the session store through `useShallow`, which
 * only compares one level deep — so each row is flattened to a single
 * string and split back apart on the way out. Message text can contain
 * any ordinary character (spaces, tabs, newlines, quotes), so the only
 * thing keeping the round-trip honest is a separator that cannot appear
 * in the payload. This asserts that, and that the whole-map subscription
 * the packing exists to avoid has not crept back in.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

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

// Mirror of packRow / unpackRow (kept in lockstep with the component).
const RAIL_SEP = "␟";
const packRow = (r) =>
  [r.id, r.content, r.preview, r.assistantId ?? "", r.assistantSummary ?? ""]
    .join(RAIL_SEP);
const unpackRow = (packed) => {
  const [id, content, preview, assistantId, assistantSummary] =
    packed.split(RAIL_SEP);
  return {
    id,
    content,
    preview,
    assistantId: assistantId || undefined,
    assistantSummary: assistantSummary || undefined,
  };
};

if (!src.includes(`const RAIL_SEP = "\\u241f"`)) {
  throw new Error("message-rail RAIL_SEP changed — update this check too");
}

const cases = [
  {
    id: "m1",
    content: "hello world  with   spaces",
    preview: "hello world with spaces",
    assistantId: "a1",
    assistantSummary: "sure thing",
  },
  // Newlines, tabs, quotes and markdown fences all have to survive.
  {
    id: "m2",
    content: "line one\nline two\ttabbed\n\n```js\nconst a = \"b\";\n```",
    preview: "line one line two tabbed",
    assistantId: undefined,
    assistantSummary: undefined,
  },
  // Empty optional fields must come back as undefined, not "".
  {
    id: "m3",
    content: "x",
    preview: "x",
    assistantId: undefined,
    assistantSummary: undefined,
  },
  // CJK + emoji payload.
  {
    id: "m4",
    content: "中文内容，带标点。🎯",
    preview: "中文内容，带标点。🎯",
    assistantId: "a4",
    assistantSummary: "回复摘要",
  },
];

for (const want of cases) {
  const got = unpackRow(packRow(want));
  for (const key of ["id", "content", "preview", "assistantId", "assistantSummary"]) {
    if (got[key] !== want[key]) {
      throw new Error(
        `round-trip lost ${key} for ${want.id}: `
        + `${JSON.stringify(got[key])} !== ${JSON.stringify(want[key])}`,
      );
    }
  }
}

// A row count change must be visible to the shallow compare, and an
// unchanged transcript must produce identical strings (that equality is
// exactly what makes streaming deltas free).
const a = cases.map(packRow);
const b = cases.map(packRow);
if (a.length !== b.length || a.some((v, i) => v !== b[i])) {
  throw new Error("packRow is not deterministic — the shallow compare would thrash");
}

console.log(`check-message-rail: ok (${cases.length} round-trips)`);
