import assert from "node:assert/strict";
import test, { after } from "node:test";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";
import { parseHTML } from "linkedom";

import { applicationRows } from "../../components/applications/application-rows.ts";

const web = dirname(fileURLToPath(new URL("../../package.json", import.meta.url)));
const temp = await mkdtemp(join(web, ".applications-page-test-"));
after(() => rm(temp, { recursive: true, force: true }));

const calculator = {
  id: "example.calculator", title: "Calculator", description: "A desktop-style calculator with history.", icon: "calculator",
  version: "2.0.0", scope: "global", enabled: false, source: "/src/calculator", digest: "d", ui: { root: "ui", entry: "index.html" },
};
const pdf = {
  id: "example.pdf-editor", title: "PDF editor", description: "Reads and edits the project's PDFs.", icon: "file-pdf",
  version: "3.1.0", scope: "project", enabled: true, source: "/src/pdf_editor", digest: "d", ui: { root: "ui", entry: "index.html" },
};
const browser = { id: "browser", enabled: true, loaded: true, entries: [], title: "Browser", description: "The built-in web browser.", icon: "browser" };

await build({
  absWorkingDir: web,
  stdin: {
    contents: 'export { ApplicationsPage } from "./components/applications/applications-page.tsx"; export { artFor, toneFor } from "./components/applications/application-icon.tsx";',
    resolveDir: web, loader: "ts",
  },
  outfile: join(temp, "page.mjs"),
  bundle: true, packages: "external", format: "esm", platform: "node", jsx: "automatic",
  loader: { ".css": "empty" }, plugins: [{ name: "services", setup(b) {
    b.onResolve({ filter: /(?:net\/applications$|net\/application-list|lib\/i18n|center-tabs-store|use-application-launcher|ui\/folder-picker|animated-icons|^next\/navigation$)/ }, a => ({ path: a.path, namespace: "mock" }));
    b.onLoad({ filter: /.*/, namespace: "mock" }, a => ({ resolveDir: web, contents:
      a.path.endsWith("net/applications") ? 'export const applicationRequest = async (...args) => { globalThis.calls.push(args); return {}; };'
      : a.path.includes("application-list") ? 'export const useApplicationList=()=>globalThis.applicationList; export const refreshApplications=async()=>[];'
      : a.path.includes("i18n") ? 'const text=(en)=>en; export const useTranslation=()=>({text,t:(k)=>k});'
      : a.path.includes("center-tabs-store") ? 'export const useCenterTabs={getState:()=>({openBuiltinTab:(page)=>globalThis.tabs.push(page)})};'
      : a.path.includes("use-application-launcher") ? 'export const useApplicationLauncher=()=>({launch:async(app)=>{globalThis.launched.push(app.id)},error:"",dialog:null});'
      : a.path.includes("folder-picker") ? 'export const useFolderPicker=()=>({pickFolder:async()=>"",folderPickerDialog:null});'
      : a.path.includes("animated-icons") ? 'const Icon=()=>null; export const BoxesIcon=Icon, FolderOpenIcon=Icon, RefreshCwIcon=Icon;'
      : 'export const useRouter=()=>({push(path){globalThis.pushed.push(path)}});'
    }));
  } }],
});
const { window, document } = parseHTML("<html><body></body></html>");
Object.assign(globalThis, { window, document, HTMLElement: window.HTMLElement, HTMLFormElement: window.HTMLFormElement, Node: window.Node, IS_REACT_ACT_ENVIRONMENT: true });
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "", display: "block" });
globalThis.requestAnimationFrame = fn => setTimeout(fn, 0);
globalThis.cancelAnimationFrame = clearTimeout;
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { ApplicationsPage, artFor, toneFor } = await import(pathToFileURL(join(temp, "page.mjs")));
const settle = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve(); }); };
const click = async element => act(async () => element.dispatchEvent(new window.Event("click", { bubbles: true })));

test("built-in and installed applications are one list with one title, description and icon", () => {
  const rows = applicationRows([pdf, calculator], [browser]);
  assert.deepEqual(rows.map(row => row.title), ["Browser", "Calculator", "PDF editor"]);
  const [builtin, calc] = rows;
  assert.equal(builtin.builtin, true);
  assert.equal(builtin.builtinTab, "browser");
  assert.equal(builtin.definition, undefined);
  assert.equal(builtin.description, "The built-in web browser.");
  assert.equal(builtin.icon, "browser");
  assert.equal(calc.builtin, false);
  assert.equal(calc.definition, calculator);
  assert.equal(calc.source, "/src/calculator");
  // A display title wins; a built-in without a title is named by its id.
  const renamed = applicationRows([{ ...pdf, display_title: "PDFs" }], [{ id: "paper_search", enabled: true, loaded: true, entries: [] }]);
  assert.deepEqual(renamed.map(row => row.title), ["Paper search", "PDFs"]);
});

test("ONLYOFFICE and Workflow Recording open their own windows", () => {
  const off = { enabled: true, loaded: true, entries: [] };
  const rows = applicationRows([], [{ ...off, id: "office_editor", title: "ONLYOFFICE" }, { ...off, id: "workflow_recording", title: "Workflow Recording" }]);
  assert.deepEqual(rows.map(row => [row.title, row.builtinTab]), [["ONLYOFFICE", "office"], ["Workflow Recording", "recording"]]);
});

test("the Launchpad tile art is the application icon", () => {
  assert.equal(artFor("browser"), "browser");
  assert.equal(artFor("file-pdf"), "pdf");
  assert.equal(artFor(undefined), "generic");
  assert.equal(toneFor("globe", "x"), "blue");
  // A package without a glyph takes a tone hashed from its name, always the same one.
  assert.equal(toneFor(undefined, "text_rewriting"), toneFor(undefined, "text_rewriting"));
});

test("the Applications page lists every application with Open, a switch and a menu", async () => {
  Object.assign(globalThis, { calls: [], tabs: [], launched: [], pushed: [], applicationList: { loaded: true, applications: [pdf, calculator], builtin: [browser] } });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(createElement(ApplicationsPage)));
    await settle();
    assert.match(host.textContent, /2 of 3 enabled/);
    const rows = [...host.querySelectorAll('[role="group"]')];
    assert.deepEqual(rows.map(row => row.getAttribute("aria-label")), ["Browser", "Calculator", "PDF editor"]);
    assert.match(rows[0].textContent, /Browserbuilt in · GlobalThe built-in web browser\./);
    assert.match(rows[2].textContent, /PDF editor3\.1\.0 · Per projectReads and edits the project's PDFs\./);
    // A disabled application dims and cannot open.
    assert.ok(rows[1].hasAttribute("data-disabled"));
    const openOf = row => [...row.querySelectorAll("button")].find(button => button.textContent === "Open");
    assert.ok(openOf(rows[1]).disabled);
    await click(openOf(rows[2]));
    assert.deepEqual(globalThis.launched, ["example.pdf-editor"]);
    await click(openOf(rows[0]));
    assert.deepEqual(globalThis.tabs, ["browser"]);
    // Every row has an icon, an enabled switch and a more menu.
    for (const row of rows) {
      assert.ok(row.querySelector("svg"), row.getAttribute("aria-label"));
      assert.ok(row.querySelector('button[role="switch"]'));
      assert.ok(row.querySelector('button[aria-label^="More for"]'));
    }
    await click(rows[1].querySelector('button[role="switch"]'));
    await settle();
    assert.deepEqual(globalThis.calls[0], ["/api/applications/example.calculator", "PATCH", { enabled: true }]);

    // The filter keeps only the built-in or the installed ones.
    const filter = label => [...host.querySelectorAll('[role="radio"]')].find(button => button.textContent === label);
    await click(filter("Built in"));
    await settle();
    assert.deepEqual([...host.querySelectorAll('[role="group"]')].map(row => row.getAttribute("aria-label")), ["Browser"]);
    await click(filter("Installed"));
    await settle();
    assert.deepEqual([...host.querySelectorAll('[role="group"]')].map(row => row.getAttribute("aria-label")), ["Calculator", "PDF editor"]);
    // The install form lives in a dialog behind Install…, not on the page.
    assert.equal(host.querySelector("#application-directory"), null);
    assert.ok([...host.querySelectorAll("button")].some(button => button.textContent === "Install…"));
  } finally { await act(async () => root.unmount()); host.remove(); }
});
