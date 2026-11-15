import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { after } from "node:test";

/**
 * A temporary directory inside the web app for a test's esbuild bundle. The
 * bundle has to sit inside the app so the bare imports it keeps resolve from
 * the app's node_modules. The directory goes when the test file finishes and
 * again when the process exits, so a bundle that fails to build (which throws
 * at module level, before any `after` hook could run) leaves nothing behind.
 */
export function scratchDir(webPath, prefix) {
  const dir = mkdtempSync(join(webPath, `.${prefix}-`));
  const clean = () => rmSync(dir, { recursive: true, force: true });
  after(clean);
  process.once("exit", clean);
  return dir;
}
