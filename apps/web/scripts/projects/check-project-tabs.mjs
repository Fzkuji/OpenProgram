// Rule: the project detail's Settings and Info tabs are built from the Settings
// page's design-system blocks (SettingsSection / SettingsRow / ui Button), with
// no inline styles and no raw <button>, so they match Settings exactly.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const root = new URL("../../", import.meta.url);
const read = (path) => readFileSync(new URL(path, root), "utf8");

const sections = {
  "components/projects/projects-page.tsx": ["SettingsSection", "SettingsRow"],
  "components/projects/project-config-section.tsx": ["SettingsSection", "SettingsRow", "SimpleSelect"],
  "components/projects/permissions-section.tsx": ["SettingsSection", "SettingsRow", "ManageIconButton", "<Button variant=\"elevated\""],
  "components/projects/computer-use-apps-section.tsx": ["SettingsSection", "ManageIconButton", "<Badge"],
};
for (const [file, parts] of Object.entries(sections)) {
  const source = read(file);
  for (const part of parts) assert.ok(source.includes(part), `${file} must use ${part}`);
  assert.ok(!/style=\{\{/.test(source), `${file} carries inline styles; use the shared blocks`);
  // The section tabs themselves keep the Agents tab-bar metrics (role="tab").
  assert.ok(!/<button\b(?![^>]*role="tab")/.test(source), `${file} has a raw <button>; use ui/button`);
}
assert.ok(!/<dl\b/.test(read("components/projects/projects-page.tsx")), "Info is a SettingsSection, not a definition list");

console.log("project tabs: ok");
