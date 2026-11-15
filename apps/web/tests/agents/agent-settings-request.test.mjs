// Opening the app or a conversation asks for the same agent settings from
// several places at once: one request answers them all, and a request that
// left before the settings changed never answers a later caller.
import assert from "node:assert/strict";
import test from "node:test";

const { agentSettingsMoved, requestAgentSettings } = await import("../../lib/agents/agent-settings-request.ts");

function reader() {
  const calls = [];
  const read = (url) => new Promise((resolve, reject) => calls.push({ url, resolve, reject }));
  return { calls, read };
}

test("callers that ask the same while a request is out share it", async () => {
  const { calls, read } = reader();
  const asked = [1, 2, 3].map(() => requestAgentSettings("?session_id=a", read));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/agent_settings?session_id=a");
  calls[0].resolve({ chat: { model: "luna" } });
  const answers = await Promise.all(asked);
  assert.ok(answers.every((answer) => answer === answers[0]));
  assert.equal(answers[0].chat.model, "luna");
});

test("another conversation or branch is another question", async () => {
  const { calls, read } = reader();
  const first = requestAgentSettings("?session_id=a&branch_id=x", read);
  const second = requestAgentSettings("?session_id=a&branch_id=y", read);
  const draft = requestAgentSettings("", read);
  assert.deepEqual(calls.map((call) => call.url), [
    "/api/agent_settings?session_id=a&branch_id=x",
    "/api/agent_settings?session_id=a&branch_id=y",
    "/api/agent_settings",
  ]);
  calls.forEach((call, index) => call.resolve({ index }));
  assert.deepEqual((await Promise.all([first, second, draft])).map((answer) => answer.index), [0, 1, 2]);
});

test("nothing is kept once the request has answered", async () => {
  const { calls, read } = reader();
  const first = requestAgentSettings("?session_id=b", read);
  calls[0].resolve({ round: 1 });
  await first;
  await Promise.resolve();
  const second = requestAgentSettings("?session_id=b", read);
  assert.equal(calls.length, 2);
  calls[1].resolve({ round: 2 });
  assert.equal((await second).round, 2);
});

test("a request that left before the settings changed does not answer a later caller", async () => {
  const { calls, read } = reader();
  const before = requestAgentSettings("?session_id=c", read);
  agentSettingsMoved();
  const after = requestAgentSettings("?session_id=c", read);
  const joined = requestAgentSettings("?session_id=c", read);
  assert.equal(calls.length, 2, "the read after the change is its own request, shared from then on");
  // The old request answering late must not take the new one off the books.
  calls[0].resolve({ model: "old" });
  assert.equal((await before).model, "old");
  await Promise.resolve();
  requestAgentSettings("?session_id=c", read);
  assert.equal(calls.length, 2);
  calls[1].resolve({ model: "new" });
  assert.equal((await after).model, "new");
  assert.equal((await joined).model, "new");
});

test("a failed request fails its callers and is not kept", async () => {
  const { calls, read } = reader();
  const first = requestAgentSettings("?session_id=d", read);
  calls[0].reject(new Error("offline"));
  await assert.rejects(first, /offline/);
  await Promise.resolve();
  requestAgentSettings("?session_id=d", read).catch(() => {});
  assert.equal(calls.length, 2);
  calls[1].resolve({});
});
