"use strict";
// Shell reports are advisory. Nonce/PID checks prevent accidental cross-session
// reports; programs inside this PTY can read the nonce and forge reports.
const PREFIX = "\x1b]777;OpenProgram;";
const MAX_FRAME = 36_000;
function createShellProtocol({ nonce, mode, pid, output, changed, timers = { setTimeout, clearTimeout } }) {
  let pending = "", frameTimer, readyTimer, closed = false;
  const state = { status: "starting", mode, reason: null, event_sequence: 0, cwd: null, cycle: null };
  const snapshot = () => structuredClone(state);
  function flush() { if (pending) output(pending); pending = ""; timers.clearTimeout(frameTimer); frameTimer = null; }
  function accept(raw) {
    const fields = raw.slice(PREFIX.length, -1).split(";");
    if (fields.length !== 8) return false;
    const [version, token, root, seqText, kind, cycleText, statusText, hex] = fields;
    const number = s => /^(0|[1-9][0-9]*)$/.test(s) && Number.isSafeInteger(Number(s));
    if (version !== "1" || token !== nonce || !number(root) || Number(root) !== pid()
      || !number(seqText) || Number(seqText) <= state.event_sequence || !number(cycleText)
      || !/^(?:[0-9a-f]{2})*$/.test(hex) || hex.length > 32768) return false;
    const sequence = Number(seqText), cycle = Number(cycleText);
    const bytes = Buffer.from(hex, "hex"), cwd = bytes.toString("utf8");
    if (hex && (!cwd.startsWith("/") || cwd.includes("\0") || !Buffer.from(cwd).equals(bytes))) return false;
    if (kind === "ready") {
      if (state.event_sequence !== 0 || cycle !== 0 || statusText || !cwd) return false;
      state.status = "ready"; state.reason = null;
      timers.clearTimeout(readyTimer);
    } else {
      if (state.status !== "ready") return false;
      if (kind === "start") {
        if (!cycle || cycle <= (state.cycle?.id || 0) || statusText || hex || state.cycle?.phase === "running") return false;
        state.cycle = { id: cycle, phase: "running", exit_code: null };
      } else if (kind === "end") {
        if (state.cycle?.phase !== "running" || cycle !== state.cycle.id || !number(statusText)
          || Number(statusText) > 255 || !cwd) return false;
        state.cycle = { id: cycle, phase: "finished", exit_code: Number(statusText) };
      } else if (kind === "cwd") {
        if (cycle !== (state.cycle?.id || 0) || statusText || !cwd || state.cycle?.phase === "running") return false;
      } else return false;
    }
    state.event_sequence = sequence;
    if (cwd) state.cwd = { value: cwd, event_sequence: sequence };
    changed(); return true;
  }
  readyTimer = timers.setTimeout(() => { if (!closed && state.status === "starting") {
    state.status = "unavailable"; state.reason = "startup_not_reported"; changed();
  } }, 3000); readyTimer?.unref?.();
  return {
    snapshot,
    feed(chunk) {
      if (closed) return output(String(chunk));
      pending += String(chunk);
      while (pending) {
        const index = pending.indexOf(PREFIX);
        if (index < 0) {
          let tail = Math.min(PREFIX.length - 1, pending.length);
          while (tail && !PREFIX.startsWith(pending.slice(-tail))) tail--;
          output(pending.slice(0, pending.length - tail)); pending = pending.slice(pending.length - tail); break;
        }
        if (index) { output(pending.slice(0, index)); pending = pending.slice(index); }
        const end = pending.indexOf("\x07", PREFIX.length);
        const restart = pending.indexOf(PREFIX, PREFIX.length);
        if (restart >= 0 && (end < 0 || restart < end)) {
          // A valid field cannot contain ESC; preserve malformed bytes, then retry the new frame.
          output(pending.slice(0, restart)); pending = pending.slice(restart); continue;
        }
        if (end < 0 && pending.length <= MAX_FRAME) break;
        if (end < 0 || end + 1 > MAX_FRAME) { output(pending.slice(0, PREFIX.length)); pending = pending.slice(PREFIX.length); continue; }
        const raw = pending.slice(0, end + 1); pending = pending.slice(end + 1);
        if (!accept(raw)) output(raw);
      }
      if (!pending) { timers.clearTimeout(frameTimer); frameTimer = null; }
      else if (!frameTimer) { frameTimer = timers.setTimeout(flush, 1000); frameTimer?.unref?.(); }
    },
    end() {
      if (closed) return; closed = true; flush(); timers.clearTimeout(readyTimer);
      state.status = "ended";
      if (state.cycle?.phase === "running") state.cycle = { ...state.cycle, phase: "unknown", exit_code: null };
    },
  };
}
module.exports = { createShellProtocol, MAX_FRAME };
