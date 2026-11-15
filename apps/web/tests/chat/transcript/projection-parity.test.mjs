/**
 * Golden parity: the server's projection turns every recorded chat and every
 * old stored shape into the rows the retired web mapper drew, apart from what
 * the projection adds on purpose. The mapper's rows are frozen in
 * mapper-rows.json; a projection change that alters what a row says fails here.
 */
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import { rowsOfTurns } from "../../../lib/chat/transcript/turn-rows.ts";
import { loadRecordings } from "../../_support/transcript-replay.mjs";

const here = (path) => fileURLToPath(new URL(path, import.meta.url));
const shapes = JSON.parse(readFileSync(here("./old-shapes.json"), "utf8"));
const recordings = loadRecordings(here("./recordings"));
const mapperRows = JSON.parse(readFileSync(here("./mapper-rows.json"), "utf8"));

/** Fields only the projection has, and why they are allowed to differ. */
const ADDED = new Set([
  "rowKind", // which dispatch branch a row takes
  "blockId", // the block that owns a child
  "coveredBy", // the compaction card folding a row
  "origin", "clientRequestId", // who wrote a user row and which send it answers
  "continues", // the cut replies a continuation joins
]);
/** Fields only the mapper had: a card named what it covered, which coveredBy now says on each row. */
const RETIRED = new Set(["coversIds"]);
const ADDED_IN_BLOCKS = new Set(["id", "index"]);

function comparable(value, inBlock = false) {
  if (Array.isArray(value)) return value.map((item) => comparable(item));
  if (!value || typeof value !== "object") return value;
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    if (item === null || item === undefined || ADDED.has(key) || RETIRED.has(key)) continue;
    if (inBlock && ADDED_IN_BLOCKS.has(key)) continue;
    if (key === "blocks" && Array.isArray(item)) out[key] = item.map((block) => comparable(block, true));
    else out[key] = comparable(item);
  }
  return out;
}

/** F43: the mapper lost pasted spans the store flattened onto the row; the projection reads them. */
const withoutPastes = ({ pastedElements: _dropped, ...row }) => row;

function sortedKeys(value) {
  if (Array.isArray(value)) return value.map(sortedKeys);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortedKeys(value[key])]));
}

/**
 * Ids and clock readings differ on every recording run, so both sides name
 * them by first appearance: a re-recorded chat still matches its frozen rows,
 * and which rows share an id or a time still has to agree.
 */
function canonical(rows) {
  const names = new Map();
  const nameOf = (token, prefix) => {
    if (!names.has(token)) names.set(token, `${prefix}${names.size}`);
    return names.get(token);
  };
  const text = JSON.stringify(sortedKeys(rows))
    .replace(/(?<=[:\[,])1[0-9]{9}(?:\.[0-9]+)?(?=[,}\]])/g, (token) => JSON.stringify(nameOf(token, "t")))
    .replace(/(?<![0-9A-Za-z])(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])[0-9a-f]{8,}(?![0-9A-Za-z])/g, (token) => nameOf(token, "id"));
  return JSON.parse(text);
}

function assertParity(name, expected, cold) {
  assert.ok(expected, `${name} has no frozen mapper rows`);
  const projected = comparable(rowsOfTurns(cold.data.turns)).map(withoutPastes);
  assert.deepEqual(canonical(projected), canonical(comparable(expected)), `${name} rows differ`);
}

for (const recording of recordings) {
  test(`projection equals the frozen mapper rows on recording ${recording.name}`, () => {
    assertParity(recording.name, mapperRows.recordings[recording.name], recording.cold[0]);
  });
}
for (const shape of shapes) {
  test(`projection equals the frozen mapper rows on old shape ${shape.name}`, () => {
    assertParity(shape.name, mapperRows.old_shapes[shape.name], shape.frames[0]);
  });
}
