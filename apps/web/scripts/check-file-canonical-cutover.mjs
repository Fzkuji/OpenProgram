import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const shared = read("lib/state/files-shared.ts");
const viewer = read("components/files/file-viewer.tsx");
const tree = read("components/files/file-tree.tsx");
const pane = read("components/center-tabs/file-tab-pane.tsx");
const review = read("components/center-tabs/review-tab-pane.tsx");
const draftState = read("lib/state/file-state-shared.ts");
const drafts = read("lib/state/file-drafts.ts");
const ws = read("lib/net/ws-request.ts");
const turnFiles = readFileSync(new URL("../../server/openprogram_server/_webui/ws_actions/turn_files.py", import.meta.url), "utf8");
const server = readFileSync(new URL("../../server/openprogram_server/server.py", import.meta.url), "utf8");

assert.doesNotMatch(shared, /filesWsRequest/);
assert.doesNotMatch(viewer, /filesWsRequest/);
assert.doesNotMatch(tree, /filesWsRequest/);
assert.doesNotMatch(pane, /filesWsRequest/);
assert.doesNotMatch(review, /getSocket|registerWsRequest|\.send\(/);
assert.doesNotMatch(ws, /filesWsRequest|list_turn_files|turn_file_diff/);
assert.doesNotMatch(ws, /registerWsRequest/);
assert.doesNotMatch(draftState, /from ["']\.\/file-drafts["']/);
assert.doesNotMatch(drafts, /from ["']\.\/files-shared["']/);
assert.doesNotMatch(turnFiles, /def _list_files\b|def handle_list_turn_files\b|def handle_turn_file_diff\b|def _turn_lineage_file_diff\b/);
assert.doesNotMatch(turnFiles, /"list_turn_files"\s*:|"turn_file_diff"\s*:/);
assert.doesNotMatch(server, /"list_turn_files"|"turn_file_diff"/);

console.log("canonical file entry-point cutover contracts: ok");
