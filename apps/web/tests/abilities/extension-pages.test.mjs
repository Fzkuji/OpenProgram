import assert from "node:assert/strict";
import { afterEach, describe, test } from "node:test";
import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render } from "../_support/dom.mjs";

const ICONS = ["PlugZapIcon", "PlusIcon", "SearchIcon", "XIcon", "FileTextIcon", "FolderCodeIcon", "FolderOpenIcon", "GraduationCapIcon"]
  .map((name) => `export const ${name} = () => null;`).join("\n");

installLoader({
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@/components/animated-icons": ICONS,
    "@/lib/net/fetch-client": "export const jsonFetch = (...args) => globalThis.__jsonFetch(...args);",
    "@/lib/abilities/skills-store": [
      "export const useSkills = () => ({",
      "  skills: globalThis.__skills ?? [],",
      "  toggleSkill: (name, enabled) => (globalThis.__toggled ??= []).push([name, enabled]),",
      "});",
    ].join("\n"),
    "@/lib/nav/shallow-nav": "export const pushPath = (path) => (globalThis.__pushed ??= []).push(path);",
  },
  replace: {
    "/components/ui/tooltip.tsx": "export const HoverTip = ({ children }) => children;",
    "/components/ui/dialog.tsx": [
      "export const Dialog = ({ open, children }) => open ? children : null;",
      "export const DialogContent = ({ children }) => children;",
      "export const DialogHeader = DialogContent;",
      "export const DialogTitle = DialogContent;",
    ].join("\n"),
    "/components/mcp/mcp-edit-dialog.tsx": "export const EditDialog = () => null;",
    "/components/ui/confirm-dialog.tsx": "export const confirmAction = async () => true;",
  },
});
installDom();
// Radix Switch checks `closest("form")` results against HTMLFormElement.
globalThis.HTMLFormElement = window.HTMLFormElement;

const { createElement } = await import("react");
const { SkillsList } = await import("../../components/skills/skills-list.tsx");
const { McpPage } = await import("../../components/mcp/mcp-page.tsx");

const byLabel = (container, label) => container.querySelector(`[aria-label="${label}"]`);
const click = (view, node) => view.act(() => { node.dispatchEvent(new window.Event("click", { bubbles: true })); });
const flush = (view) => view.act(async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); });

let view;
afterEach(async () => {
  await view?.unmount();
  view = null;
  globalThis.__toggled = [];
});

describe("Skills list switches", () => {
  const skills = [
    { name: "research/arxiv", path_segments: ["research", "arxiv"], leaf: "arxiv", enabled: true, description: "" },
    { name: "research/scholar", path_segments: ["research", "scholar"], leaf: "scholar", enabled: false, description: "" },
    { name: "write", enabled: false, description: "" },
  ];

  test("each switch names the skill or folder it turns on or off", async () => {
    globalThis.__skills = skills;
    view = await render(createElement(SkillsList));
    assert.ok(byLabel(view.container, "Enable write"));
    assert.ok(byLabel(view.container, "Enable research"), "a partly enabled folder offers to enable all");
    await click(view, view.container.querySelector("button[aria-expanded]"));
    assert.ok(byLabel(view.container, "Disable research/arxiv"));
    assert.ok(byLabel(view.container, "Enable research/scholar"));

    globalThis.__skills = skills.map((skill) => ({ ...skill, enabled: true }));
    await view.rerender(createElement(SkillsList, { externalFilter: "" }));
    assert.ok(byLabel(view.container, "Disable research"));
  });

  test("a folder switch toggles every skill in the folder; the toggle button only expands", async () => {
    globalThis.__skills = skills;
    globalThis.__toggled = [];
    view = await render(createElement(SkillsList));
    const toggle = view.container.querySelector("button[aria-expanded]");
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    await click(view, toggle);
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    assert.deepEqual(globalThis.__toggled, [], "expanding a folder changes no skill");
    await click(view, byLabel(view.container, "Enable research"));
    assert.deepEqual(globalThis.__toggled, [["research/arxiv", true], ["research/scholar", true]]);
  });
});

describe("MCP page actions", () => {
  const server = { name: "linear", type: "remote", url: "https://mcp.linear.app", enabled: true, ready: true, tool_count: 2, tools: [] };

  test("a failed server action shows its message in an alert instead of doing nothing", async () => {
    const requests = [];
    globalThis.__jsonFetch = async (url, init = {}) => {
      requests.push([url, init.method ?? "GET"]);
      if (url === "/api/mcp/servers") return { servers: [server] };
      if (url.endsWith("/disable")) throw new Error("server did not start");
      return {};
    };
    view = await render(createElement(McpPage, { embedded: true }));
    await flush(view);
    assert.equal(view.container.querySelector('[role="alert"]'), null);
    const toggle = byLabel(view.container, "Disable linear");
    assert.ok(toggle, "the server switch names its target");
    await click(view, toggle);
    await flush(view);
    assert.deepEqual(requests.at(-1), ["/api/mcp/servers/linear/disable", "POST"]);
    const alert = view.container.querySelector('[role="alert"]');
    assert.ok(alert);
    assert.equal(alert.textContent, "server did not start");
  });

  test("a failed server list load is shown as an alert", async () => {
    globalThis.__jsonFetch = async () => { throw new Error("worker unreachable"); };
    view = await render(createElement(McpPage, { embedded: true }));
    await flush(view);
    assert.equal(view.container.querySelector('[role="alert"]')?.textContent, "worker unreachable");
  });
});
