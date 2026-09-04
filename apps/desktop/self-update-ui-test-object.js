"use strict";

// Fixed isolated-world program: the model supplies neither code nor selectors.
function testObjectProgram(command) {
  const slot = "__openprogramUiTestObject";
  const dispatch = (mode) => window.dispatchEvent(new CustomEvent("op:self-update-test-object", {
    detail: { ...command, mode },
  }));
  const clear = (state) => {
    clearTimeout(state.timer);
    dispatch("abandon");
    if (globalThis[slot] === state) delete globalThis[slot];
  };
  if (command.mode === "abandon") {
    const state = globalThis[slot];
    if (state?.nonce === command.nonce) clear(state);
    return null;
  }
  const current = () => {
    const state = globalThis[slot];
    if (!state || state.nonce !== command.nonce || Date.now() >= command.deadline * 1000 ||
        location.pathname !== `/s/${command.session_id}` || !state.area.isConnected ||
        document.getElementById("chatArea") !== state.area) throw new Error("test_object_target_changed");
    return state;
  };
  const status = () => {
    current();
    const node = document.getElementById("selfUpdateTestObjectState");
    if (!node || node.getAttribute("data-nonce") !== command.nonce ||
        node.getAttribute("data-object-id") !== command.object_id) return null;
    return { phase: node.getAttribute("data-phase"), title: node.getAttribute("data-title") };
  };
  const painted = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const until = async (ready) => {
    for (;;) { await painted(); current(); if (ready()) return; }
  };
  const pageState = (area) => [area.scrollTop, area.scrollLeft, document.body.style.pointerEvents,
    document.body.style.overflow, document.body.getAttribute("data-scroll-locked")];
  if (command.mode === "start") {
    const area = document.getElementById("chatArea");
    if (globalThis[slot] || !area || area.clientHeight <= 0 || area.getBoundingClientRect().width <= 0 ||
        document.querySelector('[role="dialog"]') || document.getElementById("selfUpdateTestObjectState") ||
        location.pathname !== `/s/${command.session_id}` || Date.now() >= command.deadline * 1000) {
      throw new Error("test_object_unavailable");
    }
    const state = { nonce: command.nonce, area, before: pageState(area) };
    globalThis[slot] = state;
    state.timer = setTimeout(() => clear(state), Math.max(1, command.deadline * 1000 - Date.now()));
    dispatch("open");
    return until(() => status()?.phase === "initial" && document.getElementById("selfUpdateTestObjectDialog")).then(async () => {
      const dialog = document.getElementById("selfUpdateTestObjectDialog");
      const input = dialog.querySelector("input");
      if (status()?.title !== command.initial_title || !input || input.value !== command.initial_title ||
          dialog.querySelectorAll("input").length !== 1 || document.querySelectorAll('[role="dialog"]').length !== 1) {
        throw new Error("test_object_initial_state_changed");
      }
      state.dialog = dialog;
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, command.title);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await painted();
      current();
      if (document.getElementById("selfUpdateTestObjectDialog") !== dialog || input.value !== command.title) {
        throw new Error("test_object_input_changed");
      }
      const save = dialog.querySelector('[data-rename-action="save"]');
      if (!save || save.disabled) throw new Error("test_object_save_unavailable");
      save.click();
      await until(() => status()?.phase === "renamed" && status()?.title === command.title);
      return { before: command.initial_title, after: command.title };
    });
  }
  if (command.mode === "finish") {
    const state = current();
    if (status()?.phase !== "renamed" || status()?.title !== command.title ||
        document.getElementById("selfUpdateTestObjectDialog") !== state.dialog) throw new Error("test_object_changed");
    const cancel = state.dialog.querySelector('[data-rename-action="cancel"]');
    if (!cancel || cancel.disabled) throw new Error("test_object_cleanup_unavailable");
    cancel.click();
    return until(() => status()?.phase === "restored" && status()?.title === command.initial_title &&
      !document.getElementById("selfUpdateTestObjectDialog")).then(async () => {
      if (JSON.stringify(pageState(state.area)) !== JSON.stringify(state.before)) throw new Error("test_object_cleanup_failed");
      clear(state);
      await painted();
      if (document.getElementById("selfUpdateTestObjectState") || document.getElementById("selfUpdateTestObjectDialog")) {
        throw new Error("test_object_cleanup_failed");
      }
      return command.initial_title;
    });
  }
  throw new Error("invalid_test_object_operation");
}

function runTestObject(contents, command) {
  return contents.executeJavaScriptInIsolatedWorld(18371, [{
    code: `(${testObjectProgram.toString()})(${JSON.stringify(command)})`,
  }], false);
}

module.exports = { runTestObject };
