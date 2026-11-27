// A conversation's harness (Codex) as the composer's chips read it: which
// provider a harness's models pin, the OpenProgram models run through it,
// why a harness cannot be picked, and the agent rows its Agent chip lists.
import assert from "node:assert/strict";
import { test } from "node:test";
import { installLoader } from "../../_support/load.mjs";

installLoader();
const H = await import("../../../lib/models/harnesses.ts");

const codex = (fields = {}) => ({
  id: "codex", provider: "codex-harness", label: "Codex", installed: true, version: "0.153.4", signedIn: true,
  models: [
    { id: "gpt-5.6-sol", label: "GPT-5.6 Sol", default: false, efforts: ["low", "high"] },
    { id: "gpt-6-astra", label: "GPT-6 Astra", default: true, efforts: ["low"], personality: true },
  ],
  agents: [
    { id: "default", label: "Default", kind: "default", default: true },
    { id: "profile:fast", label: "fast", kind: "profile" },
    { id: "personality:friendly", label: "Friendly", kind: "personality" },
  ],
  ...fields,
});

test("a harness's models pin its own provider, which shows its maker's logo", () => {
  assert.equal(H.harnessProvider("codex"), "codex-harness");
  assert.equal(H.harnessOf("codex-harness"), "codex");
  assert.equal(H.harnessOf("openai"), null);
  assert.ok(H.isHarnessProvider("codex-harness"));
  assert.ok(!H.isHarnessProvider("openai"));
  assert.equal(H.harnessMark("codex-harness"), "openai");
  assert.equal(H.harnessMark("deepseek"), "deepseek");
});

test("an OpenProgram model run in a harness is openprogram:<provider>/<model>", () => {
  assert.equal(H.gatewayRef("openrouter", "a/b"), "openprogram:openrouter/a/b");
  assert.deepEqual(H.gatewayModel("openprogram:openrouter/a/b"), { provider: "openrouter", model: "a/b" });
  assert.equal(H.gatewayModel("gpt-6-astra"), null);
  assert.equal(H.gatewayModel("openprogram:openrouter/"), null);
});

test("a harness switch takes its default model; one not installed or signed out says why", () => {
  assert.equal(H.defaultModel(codex()).id, "gpt-6-astra");
  assert.equal(H.unavailable(codex()), null);
  assert.equal(H.unavailable(codex({ installed: false })), "not_installed");
  assert.equal(H.unavailable(codex({ signedIn: false })), "not_signed_in");
});

test("personalities are offered only for a model that takes one", () => {
  const ids = (model) => H.agentChoices(codex(), model).map((agent) => agent.id);
  assert.deepEqual(ids("gpt-6-astra"), ["default", "profile:fast", "personality:friendly"]);
  assert.deepEqual(ids("gpt-5.6-sol"), ["default", "profile:fast"]);
  assert.deepEqual(ids("openprogram:deepseek/deepseek-v4"), ["default", "profile:fast"]);
});
