import { readFileSync } from "node:fs";

/**
 * The composer is split across index.tsx and its submodules (chat submit,
 * fn-form dispatch, paste tokens, history recall, keydown, controls
 * cluster, status chip, drop overlay). The composer assertions in the
 * check scripts read it as ONE source text, so concatenate the parts in
 * the order the original single file had them. Slice sentinels used by
 * the assertions (wsSend, `const noop`, `function stop()`, "Pick a slash
 * command") keep their relative order under this concatenation.
 */
const COMPOSER_PARTS = [
  // use-chat-submit first: `stop()` has to precede index.tsx's
  // "Pick a slash command" marker, as it did before the split.
  "use-chat-submit.ts",
  "index.tsx",
  "paste/use-paste-tokens.ts",
  "use-history-recall.ts",
  "use-composer-keydown.ts",
  "composer-body.tsx",
  "modes/fn-form/use-fn-form-submit.ts",
  "controls/controls-cluster.tsx",
  "environment-row/status-chip.tsx",
  "scoped-drop-overlay.tsx",
];

export function readComposerSource(importMetaUrl) {
  return COMPOSER_PARTS.map((name) =>
    readFileSync(
      new URL(`../components/chat/composer/${name}`, importMetaUrl),
      "utf8",
    ),
  ).join("\n");
}
