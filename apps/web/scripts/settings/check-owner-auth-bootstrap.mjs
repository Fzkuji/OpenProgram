// Rule: the built /chat document paints the sidebar chrome before any script
// runs and never ships an "Authenticating" placeholder. It checks build output
// (apps/web/out or the wheel's staged frontend) and skips when neither exists.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

function firstPaintBody(html) {
  const start = html.search(/<body[^>]*>/i);
  const after = start >= 0 ? html.slice(start) : html;
  const scriptAt = after.search(/<script[\s>]/i);
  return scriptAt >= 0 ? after.slice(0, scriptAt) : after;
}

const exportPaths = [
  fileURLToPath(new URL("../../out/chat.html", import.meta.url)),
  fileURLToPath(new URL("../../../../openprogram/interfaces/server/_frontend/chat.html", import.meta.url)),
];
let checked = 0;
for (const path of exportPaths) {
  if (!existsSync(path)) continue;
  const html = readFileSync(path, "utf8");
  assert.doesNotMatch(html, /aria-label="Authenticating"/, `${path} must not ship an Authenticating main`);
  assert.match(firstPaintBody(html), /id="sidebar"/, `${path} first HTML must include sidebar chrome`);
  checked += 1;
}

console.log(`check-owner-auth-bootstrap: ok (${checked} built document${checked === 1 ? "" : "s"} checked)`);
