import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";
import vm from "node:vm";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const source = fs.readFileSync(new URL("../../components/settings/providers/auth/connectivity.tsx", import.meta.url), "utf8");
const javascript = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
}).outputText;

async function check(local, response) {
  let run;
  let result;
  const requests = [];
  const sandbox = {
    exports: {},
    require(id) {
      if (id === "react") return {
        forwardRef: (component) => component,
        useImperativeHandle: (_ref, handle) => { run = handle().run; },
        useState: (initial) => [initial, (value) => { if (initial === null) result = value; }],
      };
      if (id === "react/jsx-runtime") return { jsx: () => null, jsxs: () => null };
      if (id === "@/lib/i18n") return { useTranslation: () => ({ text: (english) => english }) };
      if (id.endsWith("module.css")) return { default: {} };
      return { Button: () => null };
    },
    async fetch(url, options) {
      requests.push({ url, options });
      return { json: async () => response };
    },
  };
  vm.runInNewContext(javascript, sandbox);
  sandbox.exports.Connectivity({ providerId: "ollama", local }, null);
  const ok = await run();
  assert.equal(requests[0].url, "/api/providers/ollama/validate");
  assert.equal(requests[0].options.method, "POST");
  return { ok, result };
}

for (const detail of ["Endpoint returned an invalid model listing.", "Network/timeout."]) {
  test(`local connectivity reports failure: ${detail}`, async () => {
    const { ok, result } = await check(true, { status: "unknown", ok: false, via: "GET /models", detail });
    assert.equal(ok, false);
    assert.equal(result.kind, "err");
    assert.equal(result.text, "✗ check failed");
    assert.equal(result.title, detail);
  });
}

test("local connectivity reports successful inference", async () => {
  const { ok, result } = await check(true, { status: "valid", ok: true, via: "POST /chat/completions", latency_ms: 5 });
  assert.equal(ok, true);
  assert.equal(result.kind, "ok");
});

test("cloud auth-only classification remains unchanged", async () => {
  const { ok, result } = await check(false, { status: "unknown", ok: false, via: "GET /models" });
  assert.equal(ok, false);
  assert.equal(result.kind, "info");
  assert.equal(result.text, "key accepted");
});
