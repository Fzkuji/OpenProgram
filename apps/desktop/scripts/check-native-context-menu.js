const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { createNativeContextMenus } = require("../native-context-menu");

(async () => {
  const menus = [];
  const api = createNativeContextMenus({ buildFromTemplate(template) {
    const menu = { template, popup(options) { this.options = options; }, closePopup() { this.options.callback(); } };
    menus.push(menu);
    return menu;
  } });
  const win = new EventEmitter();
  win.getContentBounds = () => ({ width: 800, height: 600 });
  const sender = new EventEmitter();
  const opts = { requestId: "one", x: 121, y: 87, items: [
    { id: "rename", label: "Rename", role: "quit" },
    { id: "group", label: "Group", separatorBefore: true, children: [{ id: "group-a", label: "A", checked: true }] },
    { id: "disabled", label: "Disabled", disabled: true },
  ] };
  const choice = api.popup(win, sender, opts, 1.5);
  const menu = menus.at(-1);
  assert.equal(menu.options.window, win);
  assert.equal(menu.options.x, 182);
  assert.equal(menu.options.y, 131);
  assert.equal(menu.template[0].role, undefined, "Renderer roles must not reach Electron");
  assert.equal(menu.template[1].type, "separator");
  assert.equal(menu.template[2].submenu[0].checked, true);
  menu.options.callback();
  menu.template[2].submenu[0].click();
  assert.equal(await choice, "group-a", "Choice is resolved after the close event cycle");
  assert.equal(sender.listenerCount("destroyed"), 0);
  assert.equal(win.listenerCount("closed"), 0);

  const cancelled = api.popup(win, sender, opts);
  api.close(sender, "stale");
  assert.equal(sender.listenerCount("destroyed"), 1, "Stale close does not cancel a new menu");
  api.close(sender, "one");
  assert.equal(await cancelled, null);

  const first = api.popup(win, sender, opts);
  const second = api.popup(win, sender, { ...opts, requestId: "two", x: -10, y: 9999 });
  assert.equal(await first, null);
  assert.equal(menus.at(-1).options.x, 0);
  assert.equal(menus.at(-1).options.y, 599);
  sender.emit("did-start-navigation", {}, "http://example.test", false, true);
  assert.equal(await second, null);

  const closing = api.popup(win, sender, opts);
  win.emit("closed");
  assert.equal(await closing, null);
  const destroyed = api.popup(win, sender, opts);
  sender.emit("destroyed");
  assert.equal(await destroyed, null);
  const disabled = api.popup(win, sender, opts);
  menus.at(-1).template[3].click();
  menus.at(-1).options.callback();
  assert.equal(await disabled, null);

  await assert.rejects(api.popup(win, sender, { ...opts, x: NaN }), /position/);
  await assert.rejects(api.popup(win, sender, { ...opts, items: [opts.items[0], opts.items[0]] }), /item/);
  await assert.rejects(api.popup(win, sender, { ...opts, items: Array.from({ length: 201 }, (_, i) => ({ id: String(i), label: "item" })) }), /item/);
  const failing = createNativeContextMenus({ buildFromTemplate() { return { popup() { throw new Error("OS unavailable"); } }; } });
  await assert.rejects(failing.popup(win, sender, opts), /OS unavailable/);
  assert.equal(sender.listenerCount("destroyed"), 0);
  assert.equal(win.listenerCount("closed"), 0);
  console.log("native-context-menu checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
