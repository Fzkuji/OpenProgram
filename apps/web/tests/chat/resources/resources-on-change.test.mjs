// A conversation's resources are read when it comes on screen and when the
// Worker says they changed (`conversation.resources`), never on a timer.
import assert from "node:assert/strict";
import test from "node:test";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

import { scratchDir } from "../../scratch-dir.mjs";

const root = dirname(fileURLToPath(new URL("../../../package.json", import.meta.url)));
const dir = scratchDir(root, "resources-on-change");
await build({
  absWorkingDir: root,
  stdin: {
    contents: `
export {readShownResourcesOnChange, CONVERSATION_RESOURCES} from './lib/browser/browser-resource-projection';
export {dispatchChanged, dispatchReconnected} from './lib/net/ws/changes';
`,
    resolveDir: root,
  },
  bundle: true, format: "esm", platform: "node", packages: "external",
  tsconfig: join(root, "tsconfig.json"), outfile: join(dir, "probe.mjs"),
});

const page = new EventTarget();
page.visibilityState = "visible";
globalThis.document = page;
const { readShownResourcesOnChange, CONVERSATION_RESOURCES, dispatchChanged, dispatchReconnected } =
  await import(pathToFileURL(join(dir, "probe.mjs")));

const settle = () => new Promise(resolve => setImmediate(resolve));

test("shown conversations are read once, then only the one that changed", async () => {
  const timers = { timeout: globalThis.setTimeout, interval: globalThis.setInterval };
  let scheduled = 0;
  globalThis.setTimeout = (...args) => { scheduled += 1; return timers.timeout(...args); };
  globalThis.setInterval = (...args) => { scheduled += 1; return timers.interval(...args); };
  const reads = [];
  let stop;
  try {
    stop = readShownResourcesOnChange(["beside", "focused"], async id => { reads.push(id); });
    await settle();
    assert.deepEqual(reads, ["beside", "focused"], "the focused conversation is read last");
    assert.equal(scheduled, 0, "nothing is scheduled to read again");

    reads.length = 0;
    dispatchChanged(CONVERSATION_RESOURCES, ["focused", "elsewhere"]);
    await settle();
    assert.deepEqual(reads, ["focused"]);

    reads.length = 0;
    dispatchChanged(CONVERSATION_RESOURCES, ["not-shown"]);
    await settle();
    assert.deepEqual(reads, []);

    dispatchChanged(CONVERSATION_RESOURCES, null);
    await settle();
    assert.deepEqual(reads, ["beside", "focused"], "a change of anything reads every shown one");

    reads.length = 0;
    dispatchReconnected();
    await settle();
    assert.deepEqual(reads, ["beside", "focused"], "a reconnect may have missed changes");
    assert.equal(scheduled, 0);
  } finally {
    stop?.();
    globalThis.setTimeout = timers.timeout;
    globalThis.setInterval = timers.interval;
  }
  reads.length = 0;
  dispatchChanged(CONVERSATION_RESOURCES, null);
  await settle();
  assert.deepEqual(reads, [], "a stopped reader reads nothing");
});

test("a hidden window holds a change and reads when it shows", async () => {
  const reads = [];
  const stop = readShownResourcesOnChange(["focused"], async id => { reads.push(id); });
  try {
    await settle();
    reads.length = 0;
    page.visibilityState = "hidden";
    dispatchChanged(CONVERSATION_RESOURCES, ["focused"]);
    await settle();
    assert.deepEqual(reads, []);
    page.visibilityState = "visible";
    page.dispatchEvent(new Event("visibilitychange"));
    await settle();
    assert.deepEqual(reads, ["focused"]);
  } finally {
    page.visibilityState = "visible";
    stop();
  }
});

test("changes during a read are read once after it", async () => {
  const reads = [];
  let release;
  const stop = readShownResourcesOnChange(["focused"], id => {
    reads.push(id);
    return reads.length === 2 ? new Promise(resolve => { release = resolve; }) : Promise.resolve();
  });
  try {
    await settle();
    dispatchChanged(CONVERSATION_RESOURCES, ["focused"]);
    await settle();
    dispatchChanged(CONVERSATION_RESOURCES, ["focused"]);
    dispatchChanged(CONVERSATION_RESOURCES, ["focused"]);
    await settle();
    assert.equal(reads.length, 2, "one read is out");
    release();
    await settle();
    await settle();
    assert.equal(reads.length, 3, "the changes that arrived meanwhile are read once");
  } finally {
    stop();
  }
});
