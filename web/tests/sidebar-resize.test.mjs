import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const sidebar = readFileSync(
  new URL("../components/sidebar/sidebar.tsx", import.meta.url),
  "utf8",
);
const shell = readFileSync(
  new URL("../components/app-shell.tsx", import.meta.url),
  "utf8",
);

test("left sidebar owns its resize state and collapse animation", () => {
  assert.match(
    sidebar,
    /const \[width, setWidth\] = useState<number>\(LEFT_W_DEFAULT\)/,
  );
  assert.match(
    sidebar,
    /style=\{open\s*\?\s*\{ width: `\$\{width\}px`, minWidth: `\$\{LEFT_W_MIN\}px` \}\s*:\s*\{ width: "49px", minWidth: "49px" \}\}/s,
  );
  assert.doesNotMatch(shell, /handleId:\s*"sidebarResize"/);
  assert.doesNotMatch(shell, /id="sidebarResize"/);
});
