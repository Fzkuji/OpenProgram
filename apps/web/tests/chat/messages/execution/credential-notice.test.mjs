import assert from "node:assert/strict";
import test from "node:test";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { scratchDir } from "../../../scratch-dir.mjs";

// The muted line a reply carries when another of the provider's credentials
// took over before it started (provider-management.html, "When a credential
// fails"): who took over, who stopped and why, one line per switch.
const web = dirname(fileURLToPath(new URL("../../../../package.json", import.meta.url)));
const dir = scratchDir(web, "credential-notice");
const file = join(dir, "credential-notice.mjs");
await build({
  absWorkingDir: web, entryPoints: ["components/chat/messages/execution/credential-notice.tsx"],
  external: ["react"], bundle: true, platform: "node", format: "esm", packages: "external", jsx: "automatic", outfile: file,
  plugins: [{ name: "notice-stubs", setup(b) {
    b.onResolve({ filter: /i18n/ }, (a) => ({ path: a.path, namespace: "stub" }));
    b.onLoad({ filter: /.*/, namespace: "stub" }, () => ({ resolveDir: web, contents: "export const useTranslation=()=>({text:(en,zh)=>globalThis.noticeLang==='zh'?zh:en});" }));
  } }],
});
const { credentialNoticeLine, CredentialNotices } = await import(pathToFileURL(file).href);
const en = (english) => english;
const zh = (_english, chinese) => chinese;

test("each cause says why the credential stopped", () => {
  const line = (cause) => credentialNoticeLine({ to: "Work", from: "Personal", cause }, en);
  assert.equal(line("rate_limit"), "Switched to Work: Personal hit a rate limit");
  assert.equal(line("quota"), "Switched to Work: Personal used up its quota");
  assert.equal(line("auth"), "Switched to Work: Personal was refused");
  assert.equal(line("model"), "Switched to Work: Personal cannot use this model");
  assert.equal(line(undefined), "Switched to Work: Personal failed");
  assert.equal(credentialNoticeLine({ to: "Work", from: "" }, en), "Switched to Work");
  assert.equal(credentialNoticeLine({ to: "Work", from: "Personal", cause: "rate_limit" }, zh), "已换到 Work：Personal 触发了速率限制");
});

test("one muted status line per switch, nothing without one", () => {
  const html = renderToStaticMarkup(createElement(CredentialNotices, { notices: [
    { to: "Work", from: "Personal", cause: "rate_limit" },
    { to: "Spare", from: "Work", cause: "quota" },
  ] }));
  const lines = [...html.matchAll(/<p class="m-0 text-fs-xs text-text-muted" role="status">([^<]*)<\/p>/g)].map((m) => m[1]);
  assert.deepEqual(lines, ["Switched to Work: Personal hit a rate limit", "Switched to Spare: Work used up its quota"]);
  assert.equal(renderToStaticMarkup(createElement(CredentialNotices, { notices: [] })), "");
  assert.equal(renderToStaticMarkup(createElement(CredentialNotices, {})), "");
});
