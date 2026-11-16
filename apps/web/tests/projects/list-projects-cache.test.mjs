// Every reader of a conversation's project list shares one answer: one on
// its way, or one already settled, until a project change drops it.
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { installLoader } from "../_support/load.mjs";
import { installDom } from "../_support/dom.mjs";

installLoader({
  replace: {
    "/lib/net/ws/ws-request.ts": [
      "export const wsRequest = async (action, payload) => { globalThis.listRequests.push([action, payload]); return globalThis.listReply(payload); };",
      "export const wsListen = () => () => {};",
      "export const wsFire = () => true;",
    ].join("\n"),
  },
});
installDom();

const { listSessionProjects, forgetProjectLists, ANSWER_TTL_MS } = await import("../../lib/projects/list-projects.ts");

beforeEach(() => {
  forgetProjectLists();
  globalThis.listRequests = [];
  globalThis.listReply = (payload) => ({ projects: [], session_id: payload.session_id || null });
});

test("readers mounting after the answer arrived reuse it: a new conversation costs one request", async () => {
  await listSessionProjects("s1");
  await listSessionProjects("s1"); // the user row's reader
  await listSessionProjects("s1"); // the reply's reader, after the first transcript update
  await Promise.all([listSessionProjects(""), listSessionProjects("")]); // the sidebar on a new session id
  assert.deepEqual(globalThis.listRequests.map(([, p]) => p.session_id), ["s1", ""]);
});

test("a project change, a reconnect or the time limit drops the kept answer", async () => {
  await listSessionProjects("s1");
  window.dispatchEvent(new Event("project-changed"));
  await listSessionProjects("s1");
  window.dispatchEvent(new Event("op:browser-connection"));
  await listSessionProjects("s1");
  assert.equal(globalThis.listRequests.length, 3);
  let clock = 1000;
  await listSessionProjects("s2", () => clock);
  clock += ANSWER_TTL_MS - 1;
  await listSessionProjects("s2", () => clock);
  assert.equal(globalThis.listRequests.length, 4);
  clock += 2;
  await listSessionProjects("s2", () => clock);
  assert.equal(globalThis.listRequests.length, 5);
});

test("no answer is not kept", async () => {
  globalThis.listReply = () => null;
  assert.equal(await listSessionProjects("s1"), null);
  globalThis.listReply = (payload) => ({ projects: [{ id: "a" }], session_id: payload.session_id });
  assert.deepEqual((await listSessionProjects("s1")).projects, [{ id: "a" }]);
  assert.equal(globalThis.listRequests.length, 2);
});
