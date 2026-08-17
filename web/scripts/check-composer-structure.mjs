import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

const root = new URL("../", import.meta.url);
const exists = (path) => existsSync(new URL(path, root));
const source = (path) => readFileSync(new URL(path, root), "utf8");

const expected = [
  "components/chat/composer/input/chat-input-row.tsx",
  "components/chat/composer/input/chat-input-row.module.css",
  "components/chat/composer/input/use-composer-keydown.ts",
  "components/chat/composer/input/use-history-recall.ts",
  "components/chat/composer/input/use-composer-input-effects.ts",
  "components/chat/composer/submit/use-chat-submit.ts",
  "components/chat/composer/submit/send-chat-message.ts",
  "components/chat/composer/modes/composer-body.tsx",
  "components/chat/composer/modes/question/question-panel.tsx",
  "components/chat/composer/modes/question/question-panel.module.css",
  "components/chat/composer/state/use-composer-settings.ts",
  "components/chat/composer/controls/use-model-availability.ts",
  "components/chat/composer/controls/use-tool-profiles.ts",
  "components/chat/composer/controls/use-unattended-mode.ts",
  "components/chat/composer/attach/scoped-drop-overlay.tsx",
  "components/chat/composer/attach/image-attach-strip.module.css",
  "components/chat/composer/paste/paste-chips.module.css",
  "components/chat/composer/environment-row/chips/connection-status-chip.tsx",
  "components/chat/composer/environment-row/chips/web-surface-chip.tsx",
];

for (const path of expected) {
  assert.equal(exists(path), true, `missing responsibility-owned file: ${path}`);
}

const composer = source("components/chat/composer/index.tsx");
const composerCss = source("components/chat/composer/composer.module.css");
assert.match(composer, /\.\/input\/use-composer-keydown/);
assert.match(composer, /\.\/input\/use-history-recall/);
assert.match(composer, /\.\/submit\/use-chat-submit/);
assert.match(composer, /\.\/submit\/send-chat-message/);
assert.match(composer, /\.\/modes\/composer-body/);
assert.match(composer, /\.\/modes\/question\/question-panel/);
assert.match(composer, /\.\/attach\/scoped-drop-overlay/);
assert.doesNotMatch(
  composerCss,
  /\.chatInput|\.pasteChip|\.imageAttach|\.questionPanel/,
);

const environmentRow = source(
  "components/chat/composer/environment-row/environment-row.tsx",
);
assert.match(environmentRow, /trailingControls\??:\s*ReactNode/);
assert.doesNotMatch(environmentRow, /dagHudSlot|DAG/i);

console.log("composer structure checks passed");
