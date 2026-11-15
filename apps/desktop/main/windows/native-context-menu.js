// Native menus return a choice to the owning renderer; they never execute
// renderer-provided roles, code, URLs, or filesystem operations.
function createNativeContextMenus(Menu) {
  const active = new Map();
  function close(sender, requestId) {
    const request = active.get(sender);
    if (request && (requestId === undefined || request.id === requestId)) request.cancel();
  }
  function popup(win, sender, opts, zoom = 1) {
    if (!opts || typeof opts.requestId !== "string" || opts.requestId.length > 128) {
      return Promise.reject(new Error("Invalid menu request"));
    }
    let count = 0;
    let chosen = null;
    const ids = new Set();
    function template(items, depth = 0) {
      if (!Array.isArray(items) || depth > 4) throw new Error("Invalid menu items");
      return items.flatMap((item) => {
        if (++count > 200 || !item || typeof item.id !== "string"
          || !item.id || item.id.length > 256 || ids.has(item.id)
          || typeof item.label !== "string" || item.label.length > 512) {
          throw new Error("Invalid menu item");
        }
        ids.add(item.id);
        const entry = { label: item.label, enabled: item.disabled !== true };
        if (item.children) entry.submenu = template(item.children, depth + 1);
        else {
          if (typeof item.checked === "boolean") {
            entry.type = "checkbox";
            entry.checked = item.checked;
          }
          entry.click = () => { if (entry.enabled) chosen = item.id; };
        }
        return item.separatorBefore ? [{ type: "separator" }, entry] : [entry];
      });
    }
    let menu;
    try { menu = Menu.buildFromTemplate(template(opts.items)); }
    catch (error) { return Promise.reject(error); }
    if (!Number.isFinite(opts.x) || !Number.isFinite(opts.y)) {
      return Promise.reject(new Error("Invalid menu position"));
    }
    close(sender);
    return new Promise((resolve, reject) => {
      let done = false;
      const finish = (choice, error) => {
        if (done) return;
        done = true;
        if (active.get(sender) === request) active.delete(sender);
        win.removeListener("closed", cancel);
        sender.removeListener("destroyed", cancel);
        sender.removeListener("did-start-navigation", navigate);
        if (error) reject(error); else resolve(choice);
      };
      const cancel = () => {
        finish(null);
        try { menu.closePopup(win); } catch { /* window already destroyed */ }
      };
      const navigate = (_event, _url, _inPlace, mainFrame) => { if (mainFrame) cancel(); };
      const request = { id: opts.requestId, cancel };
      active.set(sender, request);
      win.once("closed", cancel);
      sender.once("destroyed", cancel);
      sender.on("did-start-navigation", navigate);
      const bounds = win.getContentBounds();
      const scale = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
      try {
        menu.popup({
          window: win,
          x: Math.round(Math.max(0, Math.min(bounds.width - 1, opts.x * scale))),
          y: Math.round(Math.max(0, Math.min(bounds.height - 1, opts.y * scale))),
          // Electron may emit the item click in the same closing event cycle.
          callback: () => setImmediate(() => finish(chosen)),
        });
      } catch (error) {
        finish(null, error);
      }
    });
  }
  return { popup, close };
}
module.exports = { createNativeContextMenus };
