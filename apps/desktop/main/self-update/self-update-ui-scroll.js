"use strict";

// This fixed program runs in a private isolated world. The verifier supplies no
// JavaScript or selector; only an already approved scroll or perspective enters.
function scrollProgram(command) {
  const slot = "__openprogramUiScroll";
  const marker = "data-self-update-verification";
  const metrics = (area) => ({ top: area.scrollTop, left: area.scrollLeft,
    height: area.scrollHeight, viewport: area.clientHeight });
  const clear = (state) => {
    clearTimeout(state.timer);
    if (state.area.getAttribute(marker) === state.nonce) {
      state.area.style.scrollBehavior = state.behavior;
      state.area.removeAttribute(marker);
    }
    if (state.toggle?.getAttribute("data-self-update-view") === state.nonce) state.toggle.removeAttribute("data-self-update-view");
    if (globalThis[slot] === state) delete globalThis[slot];
  };
  const current = () => {
    const state = globalThis[slot];
    if (!state || state.nonce !== command.nonce || !state.area.isConnected ||
        document.getElementById("chatArea") !== state.area ||
        location.pathname !== `/s/${command.session_id}` ||
        state.area.getAttribute(marker) !== command.nonce || Date.now() >= command.deadline * 1000) {
      throw new Error("scroll_target_changed");
    }
    if (state.toggle && (document.getElementById("sessionPerspectiveToggle") !== state.toggle ||
        state.toggle.getAttribute("data-session-id") !== command.session_id ||
        state.toggle.getAttribute("data-tab-id") !== state.tab ||
        state.toggle.getAttribute("data-self-update-view") !== state.nonce ||
        state.toggle.closest("[data-center-view]") !== state.pane)) throw new Error("view_target_changed");
    return state;
  };
  const painted = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  if (command.mode === "abandon") {
    const state = globalThis[slot];
    if (state?.nonce === command.nonce) clear(state);
    return null;
  }
  if (command.mode === "start") {
    const area = document.getElementById("chatArea");
    const messages = document.getElementById("chatMessages");
    if (globalThis[slot] || !area || !messages || !area.contains(messages) ||
        document.querySelectorAll("#chatArea").length !== 1 || area.hasAttribute(marker) ||
        area.clientHeight <= 0 || area.getBoundingClientRect().width <= 0 ||
        location.pathname !== `/s/${command.session_id}` || Date.now() >= command.deadline * 1000) {
      throw new Error("scroll_target_unavailable");
    }
    const state = { area, nonce: command.nonce, before: metrics(area), behavior: area.style.scrollBehavior };
    if (command.kind === "view") {
      const toggle = document.getElementById("sessionPerspectiveToggle");
      const pane = toggle?.closest("[data-center-view]");
      // Start from the restored transcript, never a hidden/other session pane.
      if (!toggle || toggle.disabled || toggle.getAttribute("data-session-id") !== command.session_id ||
          !toggle.getAttribute("data-tab-id") || toggle.hasAttribute("data-self-update-view") ||
          document.querySelectorAll("#sessionPerspectiveToggle").length !== 1 ||
          !pane?.contains(area) || pane.getAttribute("data-center-view") !== "session" ||
          toggle.getAttribute("aria-pressed") !== "false") throw new Error("view_target_unavailable");
      Object.assign(state, { toggle, pane, tab: toggle.getAttribute("data-tab-id"), target: command.target });
      toggle.setAttribute("data-self-update-view", command.nonce);
    }
    globalThis[slot] = state;
    area.setAttribute(marker, command.nonce);
    area.style.scrollBehavior = "auto";
    state.timer = setTimeout(() => clear(state), Math.max(1, command.deadline * 1000 - Date.now()));
    if (state.toggle) {
      if (command.target === "dag") state.toggle.click();
      return painted().then(() => {
        current();
        if (state.pane.getAttribute("data-center-view") !== command.target ||
            state.toggle.getAttribute("aria-pressed") !== String(command.target === "dag")) throw new Error("view_not_changed");
        return { before: "session", after: command.target };
      });
    }
    area.scrollTop = Math.max(0, Math.min(area.scrollHeight - area.clientHeight, area.scrollTop + command.delta_y));
    return painted().then(() => ({ before: current().before, after: metrics(area) }));
  }
  if (command.mode === "finish") {
    const state = current();
    if (state.toggle) {
      if (state.pane.getAttribute("data-center-view") !== state.target) throw new Error("view_target_changed");
      if (state.target === "dag") state.toggle.click();
      return painted().then(() => {
        current();
        state.area.scrollTop = state.before.top;
        state.area.scrollLeft = state.before.left;
        return painted();
      }).then(() => {
        current();
        if (state.pane.getAttribute("data-center-view") !== "session" ||
            state.toggle.getAttribute("aria-pressed") !== "false" ||
            JSON.stringify(metrics(state.area)) !== JSON.stringify(state.before)) throw new Error("view_restore_failed");
        clear(state);
        return "session";
      });
    }
    state.area.scrollTop = state.before.top;
    return painted().then(() => {
      current();
      const restored = metrics(state.area);
      clear(state);
      return restored;
    });
  }
  throw new Error("invalid_scroll_operation");
}

function runScroll(contents, command) {
  return contents.executeJavaScriptInIsolatedWorld(18371, [{
    code: `(${scrollProgram.toString()})(${JSON.stringify(command)})`,
  }], false);
}

module.exports = { runScroll };
