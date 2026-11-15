import assert from "node:assert/strict";
import { afterEach, beforeEach, describe, test } from "node:test";

import { installLoader, STUBS } from "../_support/load.mjs";
import { installDom, render, resizeObservers } from "../_support/dom.mjs";

// xterm needs a real canvas; this fake records what the page does to it.
const XTERM = `
export class Terminal {
  constructor(options) {
    this.options = { ...options };
    this.cols = 80; this.rows = 24;
    this.written = []; this.cleared = 0; this.disposed = false; this.selection = "";
    this.keyHandler = null; this.dataHandler = null;
    globalThis.__terminals.push(this);
  }
  loadAddon() {}
  open(host) { this.host = host; }
  onData(cb) { this.dataHandler = cb; return { dispose() {} }; }
  onSelectionChange() { return { dispose() {} }; }
  attachCustomKeyEventHandler(handler) { this.keyHandler = handler; }
  write(data) { this.written.push(data); }
  writeln(line) { this.written.push(line + "\\n"); }
  clear() { this.cleared += 1; }
  focus() {}
  getSelection() { return this.selection; }
  hasSelection() { return Boolean(this.selection); }
  dispose() { this.disposed = true; }
}`;

const CONTEXT_MENU = `
import { createElement as h } from "react";
const pass = ({ children }) => h("div", null, children);
export const ContextMenu = pass;
export const ContextMenuTrigger = ({ children }) => children;
export const ContextMenuContent = ({ children }) => h("div", { role: "menu" }, children);
export const ContextMenuItem = ({ children }) => h("div", { role: "menuitem" }, children);
export const ContextMenuSeparator = () => h("hr");
`;

installLoader({
  replace: { "/components/ui/context-menu.tsx": CONTEXT_MENU },
  stubs: {
    "@/lib/i18n": STUBS.i18n,
    "@xterm/xterm": XTERM,
    "@xterm/addon-fit": "export class FitAddon { fit() {} }",
    "@/lib/desktop/desktop-bridge": [
      "export const desktopBridge = () => globalThis.__bridge ?? null;",
      "export const desktopTerminalId = (bridge, preset) => `terminal:${bridge.windowId}:${preset}`;",
    ].join("\n"),
    "@/lib/files/files-shared": "export const useCurrentProject = () => globalThis.__project;",
    "@/components/solar-icons": "export const SolarIcon = () => null;",
    "@/components/chat/messages/editing/quote-to-chat": "export const appendToComposer = () => {};",
    "@/lib/chat/resources/target-chat": "export const targetChat = () => null;",
    "@/lib/chat/resources/terminal-selection": "export const terminalSelectionText = (text) => text;",
    "@/lib/format-utils/toast": "export const showToast = () => {};",
  },
});
const window = installDom();
globalThis.MutationObserver = window.MutationObserver ?? class { observe() {} disconnect() {} };
// Theme colours come back as the `var(--token, fallback)` the page asked for.
globalThis.getComputedStyle = (element) => ({
  color: element.style?.color ?? "",
  getPropertyValue: () => "",
});

const { createElement } = await import("react");
const { TerminalPage } = await import("@/components/center-tabs/pages/terminal-page");

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function setPlatform(platform) {
  Object.defineProperty(globalThis.navigator, "platform", { value: platform, configurable: true });
}
const clipboard = { written: [], text: "" };
Object.defineProperty(globalThis.navigator, "clipboard", {
  configurable: true,
  value: {
    writeText: async (value) => { clipboard.written.push(value); },
    readText: async () => clipboard.text,
  },
});

function fakeApi({ startResult = { ok: true, pid: 4321 } } = {}) {
  const calls = { start: [], write: [], resize: [], stop: [] };
  let emit = () => {};
  return {
    calls,
    emit: (payload) => emit(payload),
    api: {
      start: async (request) => { calls.start.push(request); return startResult; },
      write: (id, data) => calls.write.push([id, data]),
      resize: (id, cols, rows) => calls.resize.push([id, cols, rows]),
      stop: (id) => calls.stop.push(id),
      onData: (cb) => { emit = cb; return () => { emit = () => {}; }; },
    },
  };
}

async function mount({ preset = "shell", project = { path: "/work/repo" }, ...options } = {}) {
  const terminal = fakeApi(options);
  globalThis.__bridge = { windowId: "w1", terminal: terminal.api };
  globalThis.__project = project;
  const view = await render(createElement(TerminalPage, { preset }));
  await view.act(async () => { await flush(); await flush(); });
  return { view, terminal, xterm: globalThis.__terminals.at(-1) };
}

function key(xterm, init) {
  return xterm.keyHandler({ type: "keydown", key: "c", metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...init });
}
const button = (root, label) => root.querySelector(`button[aria-label="${label}"]`);
const statusText = (root) => root.querySelector("[role=status]").textContent;

beforeEach(() => {
  globalThis.__terminals = [];
  clipboard.written = [];
  clipboard.text = "";
  setPlatform("MacIntel");
});
afterEach(() => {
  delete globalThis.__bridge;
});

describe("starting", () => {
  test("without the desktop app the page explains that terminals need it", async () => {
    globalThis.__project = null;
    const view = await render(createElement(TerminalPage, { preset: "shell" }));
    assert.match(view.container.textContent, /Local terminals require the desktop app/);
    assert.equal(globalThis.__terminals.length, 0);
    await view.unmount();
  });

  test("the process starts in the current project with the terminal's size and records its pid", async () => {
    const { view, terminal } = await mount({ preset: "claude" });
    assert.deepEqual(terminal.calls.start, [{ id: "terminal:w1:claude", cwd: "/work/repo", preset: "claude", cols: 80, rows: 24 }]);
    assert.equal(statusText(view.container), "Running");
    assert.equal(view.container.querySelector("[aria-label='Claude Code terminal']").dataset.processId, "4321");
    await view.unmount();
  });

  test("with no project the process starts in the home directory", async () => {
    const { view, terminal } = await mount({ project: null });
    assert.equal(terminal.calls.start[0].cwd, undefined);
    assert.match(view.container.textContent, /Home directory/);
    await view.unmount();
  });

  test("a start failure shows the error and stops taking input", async () => {
    const { view, xterm } = await mount({ startResult: { ok: false, error: "spawn_failed" } });
    assert.equal(statusText(view.container), "Error");
    assert.equal(xterm.options.disableStdin, true);
    assert.ok(xterm.written.includes("Terminal unavailable: spawn_failed\n"));
    await view.unmount();
  });

  test("every ANSI colour of the xterm theme comes from its --terminal-* token", async () => {
    const { view, xterm } = await mount();
    const names = [
      "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
      "brightBlack", "brightRed", "brightGreen", "brightYellow", "brightBlue", "brightMagenta", "brightCyan", "brightWhite",
    ];
    for (const name of names) {
      const token = `--terminal-${name.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`;
      assert.equal(xterm.options.theme[name], `var(${token})`, `${name} reads ${token}`);
    }
    assert.equal(xterm.options.theme.background, "var(--terminal-bg)");
    await view.unmount();
  });
});

describe("input and output", () => {
  test("typed data goes to the process and process output is written to the terminal", async () => {
    const { view, terminal, xterm } = await mount();
    xterm.dataHandler("ls\r");
    assert.deepEqual(terminal.calls.write, [["terminal:w1:shell", "ls\r"]]);
    await view.act(() => terminal.emit({ id: "terminal:w1:shell", data: "file.txt\r\n" }));
    await view.act(() => terminal.emit({ id: "terminal:other:shell", data: "not mine" }));
    assert.ok(xterm.written.includes("file.txt\r\n"));
    assert.equal(xterm.written.includes("not mine"), false);
    await view.unmount();
  });

  test("Ctrl+C sends an interrupt byte instead of copying", async () => {
    const { view, terminal, xterm } = await mount();
    assert.equal(key(xterm, { ctrlKey: true }), false);
    assert.deepEqual(terminal.calls.write, [["terminal:w1:shell", "\x03"]]);
    assert.equal(key(xterm, { key: "a", ctrlKey: true }), true, "other keys go to xterm");
    await view.unmount();
  });

  test("Cmd+C or Ctrl+Shift+C copies the selection; Cmd+V pastes the clipboard into the process", async () => {
    const { view, terminal, xterm } = await mount();
    xterm.selection = "hello";
    clipboard.text = "pasted";
    await view.act(async () => {
      assert.equal(key(xterm, { metaKey: true }), false);
      assert.equal(key(xterm, { ctrlKey: true, shiftKey: true }), false);
      assert.equal(key(xterm, { key: "v", metaKey: true }), false);
      await flush();
    });
    assert.deepEqual(clipboard.written, ["hello", "hello"]);
    assert.deepEqual(terminal.calls.write, [["terminal:w1:shell", "pasted"]]);
    await view.unmount();
  });

  test("Cmd+K and the Clear button wipe the screen and the scrollback", async () => {
    const { view, xterm } = await mount();
    assert.equal(key(xterm, { key: "k", metaKey: true }), false);
    assert.equal(xterm.cleared, 1);
    assert.equal(xterm.written.at(-1), "\x1b[2J\x1b[3J\x1b[H");
    await view.act(() => button(view.container, "Clear terminal").click());
    assert.equal(xterm.cleared, 2);
    assert.equal(xterm.written.at(-1), "\x1b[2J\x1b[3J\x1b[H");
    await view.unmount();
  });

  test("a resize of the host refits and tells the process the new size", async () => {
    const { view, terminal, xterm } = await mount();
    xterm.cols = 120;
    xterm.rows = 40;
    resizeObservers.trigger(view.container.querySelector("[aria-label='Terminal']"));
    assert.deepEqual(terminal.calls.resize, [["terminal:w1:shell", 120, 40]]);
    await view.unmount();
  });
});

describe("process lifetime", () => {
  test("when the process exits the terminal shows the code and stops taking input", async () => {
    const { view, terminal, xterm } = await mount();
    await view.act(() => terminal.emit({ id: "terminal:w1:shell", data: "", done: true, exitCode: 2 }));
    assert.equal(statusText(view.container), "Exited 2");
    assert.equal(xterm.options.disableStdin, true);
    assert.ok(xterm.written.includes("[process exited: 2]\n"));
    assert.equal(button(view.container, "Stop process").disabled, true);
    assert.equal(button(view.container, "Paste").disabled, true);
    await view.unmount();
  });

  test("Stop ends the process and the terminal stops taking input", async () => {
    const { view, terminal, xterm } = await mount();
    await view.act(() => button(view.container, "Stop process").click());
    assert.deepEqual(terminal.calls.stop, ["terminal:w1:shell"]);
    assert.equal(statusText(view.container), "Stopped");
    assert.equal(xterm.options.disableStdin, true);
    await view.unmount();
  });

  test("Restart stops the old process and starts a fresh terminal", async () => {
    const { view, terminal, xterm } = await mount();
    await view.act(async () => { button(view.container, "Restart process").click(); await flush(); await flush(); });
    assert.deepEqual(terminal.calls.stop, ["terminal:w1:shell"]);
    assert.equal(terminal.calls.start.length, 2);
    assert.equal(xterm.disposed, true);
    assert.equal(globalThis.__terminals.length, 2);
    assert.equal(statusText(view.container), "Running");
    await view.unmount();
  });

  test("unmounting the page disposes the view but leaves the process running", async () => {
    const { view, terminal, xterm } = await mount();
    await view.unmount();
    assert.equal(xterm.disposed, true);
    assert.deepEqual(terminal.calls.stop, []);
  });
});

describe("context menu keys follow the platform", () => {
  const kbds = (root) => [...root.querySelectorAll("[role=menu] kbd")].map((kbd) => kbd.textContent);

  test("macOS shows ⌘C and ⌘V", async () => {
    const { view } = await mount();
    assert.deepEqual(kbds(view.container), ["⌘C", "⌘V"]);
    await view.unmount();
  });

  test("elsewhere copy and paste take Shift, since Ctrl+C interrupts", async () => {
    setPlatform("Linux x86_64");
    const { view } = await mount();
    assert.deepEqual(kbds(view.container), ["Ctrl+Shift+C", "Ctrl+Shift+V"]);
    await view.unmount();
  });
});
