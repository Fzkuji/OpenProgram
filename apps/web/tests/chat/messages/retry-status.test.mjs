import assert from "node:assert/strict";
import test from "node:test";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { scratchDir } from "../../scratch-dir.mjs";

// The retry state in a reply (chat-turn-visual-spec.html §1): what the tag
// and the timeline row say, and that the row's body is the failure.
const web = dirname(fileURLToPath(new URL("../../../package.json", import.meta.url)));
const dir = scratchDir(web, "retry-status");
const file = join(dir, "retry-status.mjs");
await build({
  absWorkingDir: web, entryPoints: ["components/chat/messages/execution/retry-status.tsx"],
  external: ["react"], bundle: true, platform: "node", format: "esm", packages: "external", jsx: "automatic", outfile: file,
  plugins: [{ name: "retry-stubs", setup(b) {
    b.onResolve({ filter: /i18n|execution-strip/ }, (a) => ({ path: a.path, namespace: "stub" }));
    b.onLoad({ filter: /.*/, namespace: "stub" }, (a) => ({ resolveDir: web, contents:
      a.path.includes("i18n")
        ? "export const useTranslation=()=>({text:(en,zh)=>globalThis.retryLang==='zh'?zh:en});"
        : 'import {createElement} from "react"; export const StepRow=(p)=>createElement("div",{className:"step","data-glyph":p.glyph,"data-running":String(!!p.running)},p.title,"|",p.note||"",p.inlineBody); export const ExecutionStrip=({label,status,children})=>createElement("section",null,label,status,children);',
    }));
  } }],
});
const { retryLabel, retryIsFailure, RetryTag, RetryStep, RetryStrip } = await import(pathToFileURL(file).href);
const en = (english) => english;
const zh = (_english, chinese) => chinese;

test("the label says what failed, when the next try comes and which try it is", () => {
  const transport = { attempt: 2, maxAttempts: 5, reason: "transport" };
  assert.equal(retryLabel(transport, 3, zh), "连接中断，3 秒后重试 2/5");
  assert.equal(retryLabel(transport, 0, zh), "连接中断，正在重试 2/5");
  assert.equal(retryLabel(transport, 3, en), "Connection lost, retrying in 3s · 2/5");
  // The cause comes from the failure when it names one.
  assert.equal(retryLabel({ ...transport, error: "InternalServerError: Error code: 503 - overloaded" }, 3, zh), "服务器繁忙，3 秒后重试 2/5");
  assert.equal(retryLabel({ ...transport, error: "RateLimitError: Error code: 429" }, 0, en), "Rate limited, retrying 2/5");
  assert.equal(retryLabel({ ...transport, error: "ReadTimeout: timed out" }, 0, zh), "请求超时，正在重试 2/5");
  assert.equal(retryLabel({ ...transport, error: "RemoteProtocolError: peer closed connection" }, 0, zh), "连接中断，正在重试 2/5");
  assert.equal(retryLabel({ attempt: 4, reason: "network_wait" }, 20, zh), "网络断了，20 秒后再试");
  assert.equal(retryLabel({ attempt: 4, reason: "network_wait" }, 0, en), "Offline, checking again");
  assert.equal(retryLabel({ attempt: 2, reason: "output_limit" }, 0, zh), "输出到上限，继续生成");
  assert.equal(retryLabel({ attempt: 2, maxAttempts: 3, reason: "incomplete_response" }, 0, zh), "回复不完整，正在重试 2/3");
  assert.equal(retryLabel({ attempt: 2 }, 0, en), "Reply came back incomplete, retrying 2");
});

test("only a continuation is not a failure", () => {
  assert.equal(retryIsFailure({ attempt: 2, reason: "transport" }), true);
  assert.equal(retryIsFailure({ attempt: 2, reason: "network_wait" }), true);
  assert.equal(retryIsFailure({ attempt: 2 }), true);
  assert.equal(retryIsFailure({ attempt: 2, reason: "output_limit" }), false);
});

test("the countdown runs from when the notice arrived", () => {
  globalThis.retryLang = "zh";
  const retry = { attempt: 2, maxAttempts: 5, reason: "transport", delayMs: 8000, receivedAt: Date.now() - 3000 };
  assert.match(renderToStaticMarkup(createElement(RetryTag, { retry })), /连接中断，5 秒后重试 2\/5/);
  const due = { ...retry, receivedAt: Date.now() - 9000 };
  assert.match(renderToStaticMarkup(createElement(RetryTag, { retry: due })), /连接中断，正在重试 2\/5/);
});

test("the tag follows the sentence, and stands alone as a row of its own", () => {
  globalThis.retryLang = "en";
  const retry = { attempt: 2, maxAttempts: 5, reason: "transport" };
  const tag = renderToStaticMarkup(createElement(RetryTag, { retry }));
  assert.match(tag, /^<span class="tl-retry is-failure" aria-hidden="true">·<\/span><span class="tl-retry is-failure" role="status"/);
  const strip = renderToStaticMarkup(createElement(RetryStrip, { retry }));
  assert.doesNotMatch(strip, />·</);
  assert.match(strip, /class="step"/);
  const going = renderToStaticMarkup(createElement(RetryTag, { retry: { attempt: 2, reason: "output_limit" } }));
  assert.doesNotMatch(going, /is-failure/);
});

test("the row runs and opens to the failure", () => {
  globalThis.retryLang = "en";
  const row = renderToStaticMarkup(createElement(RetryStep, {
    retry: { attempt: 2, maxAttempts: 5, reason: "transport", error: "ReadTimeout: no data in 120s" },
  }));
  assert.match(row, /data-glyph="plug-circle" data-running="true">Timed out, retrying 2\/5\|ReadTimeout: no data in 120s<div class="tl-retry-error">ReadTimeout: no data in 120s<\/div>/);
  const bare = renderToStaticMarkup(createElement(RetryStep, { retry: { attempt: 2, reason: "transport" } }));
  assert.doesNotMatch(bare, /tl-retry-error/);
});
