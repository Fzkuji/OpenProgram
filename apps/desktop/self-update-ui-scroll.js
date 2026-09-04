"use strict";

// This fixed program runs in a private isolated world. The verifier supplies no
// JavaScript or selector; only the already approved bounded scroll delta enters.
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
    globalThis[slot] = state;
    area.setAttribute(marker, command.nonce);
    area.style.scrollBehavior = "auto";
    state.timer = setTimeout(() => clear(state), Math.max(1, command.deadline * 1000 - Date.now()));
    area.scrollTop = Math.max(0, Math.min(area.scrollHeight - area.clientHeight, area.scrollTop + command.delta_y));
    return painted().then(() => ({ before: current().before, after: metrics(area) }));
  }
  if (command.mode === "finish") {
    const state = current();
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
