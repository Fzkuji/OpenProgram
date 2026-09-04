"use strict";

// Renderer origin is not permission to invoke native operations during a check.
// Keep every IPC registration behind the same guard, including future channels.
function createUiVerificationGuard(nativeIpcMain) {
  const active = new Map();
  const sessions = new WeakSet();
  const blocked = (contents) => Boolean(contents && active.get(contents.id)?.contents === contents);
  const ipcMain = new Proxy(nativeIpcMain, {
    get(target, key) {
      if (key === "on" || key === "handle") return (channel, listener) => {
        const result = target[key](channel, function (...args) {
          const sender = args[0]?.sender;
          if (blocked(sender)) {
            if (key === "handle") throw new Error("native operation unavailable during UI verification");
            return;
          }
          return listener.apply(this, args);
        });
        return key === "on" ? ipcMain : result;
      };
      const value = Reflect.get(target, key);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
  function acquire(contents) {
    if (!contents || contents.isDestroyed() || !Number.isInteger(contents.id) || contents.id <= 0 ||
        typeof contents.session?.webRequest?.onBeforeRequest !== "function") {
      throw new Error("main-window request guard unavailable");
    }
    if (active.has(contents.id)) throw new Error("main window already guarded");
    const session = contents.session;
    if (!sessions.has(session)) {
      // Desktop owns this session hook. Do not install another onBeforeRequest
      // listener elsewhere: Electron retains only the last listener for it.
      session.webRequest.onBeforeRequest((details, callback) => {
        const lease = active.get(details.webContentsId);
        callback({ cancel: Boolean(lease && lease.contents.session === session) });
      });
      sessions.add(session);
    }
    const lease = { contents };
    active.set(contents.id, lease);
    return () => {
      if (active.get(contents.id) === lease) active.delete(contents.id);
    };
  }
  return { ipcMain, acquire, blocked };
}

module.exports = { createUiVerificationGuard };
