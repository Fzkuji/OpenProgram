// Rule: Agent pages draw icons from the shared animated icon set, never lucide-react or hand-written SVG.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";

const dir = new URL("../../components/agents/", import.meta.url);
for (const name of readdirSync(dir).filter((file) => /\.tsx?$/.test(file))) {
  assert.doesNotMatch(
    readFileSync(new URL(name, dir), "utf8"),
    /from ["']lucide-react["']|<svg\b|<path\b/,
    `components/agents/${name} must reuse the animated icon repository`,
  );
}
console.log("agent icon checks passed");
