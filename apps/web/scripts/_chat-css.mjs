// `app/styles/chat.css` and `right-dock.css` were each split into one file
// per concern under a directory of the same name. The contract checks assert
// against the rules, not the file layout, so they read a directory
// concatenated back into one string.
// ponytail: plain concat — every assertion matches or slices on selectors,
// never on cascade precedence, so file order doesn't matter.
import { readdirSync, readFileSync } from "node:fs";

function readDir(root, dir) {
  const url = new URL(dir, root);
  return readdirSync(url)
    .filter((f) => f.endsWith(".css"))
    .sort()
    .map((f) => readFileSync(new URL(f, url), "utf8"));
}

// `dag/` rides along with chat: the transcript-vs-graph rules the center-tab
// checks assert on (`[data-center-view="dag"]`) moved there in the same split.
export function readChatCss(root) {
  return [
    ...readDir(root, "app/styles/chat/"),
    ...readDir(root, "app/styles/dag/"),
  ].join("\n");
}

export function readRightDockCss(root) {
  return readDir(root, "app/styles/right-dock/").join("\n");
}
