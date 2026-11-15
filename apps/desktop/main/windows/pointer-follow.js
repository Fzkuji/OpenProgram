// Follows a held right or middle mouse button past the window's edge.
//
// On macOS a page stops hearing a right or middle drag once the cursor leaves
// the window, release included. So the main process reads the cursor itself,
// and a short-lived system script waits for the physical button to come up.
// The script exits by itself on release, so a crash here cannot leave it running.

// The same bits in NSEvent.pressedMouseButtons and PointerEvent.buttons.
const FOLLOWED_BUTTONS = new Set([2, 4]);
const CURSOR_POLL_MS = 16;
const OSASCRIPT = "/usr/bin/osascript";

function waitForReleaseScript(held) {
  return `ObjC.import("AppKit"); while ($.NSEvent.pressedMouseButtons & ${held}) delay(0.02);`;
}

function createPointerFollow({ ipcMain, screen, spawn, platform = process.platform }) {
  const active = new Map();

  function start(wc, held) {
    active.get(wc.id)?.();
    let last = null;
    const timer = setInterval(() => {
      const point = screen.getCursorScreenPoint();
      if (last && last.x === point.x && last.y === point.y) return;
      last = point;
      // The page pans in its own CSS pixels, which View > Zoom scales.
      const zoom = wc.getZoomFactor() || 1;
      wc.send("pointer-follow:cursor", { x: point.x / zoom, y: point.y / zoom });
    }, CURSOR_POLL_MS);
    const helper = spawn(OSASCRIPT, ["-l", "JavaScript", "-e", waitForReleaseScript(held)], {
      stdio: "ignore",
    });
    const finish = () => {
      if (active.get(wc.id) !== cancel) return false;
      active.delete(wc.id);
      clearInterval(timer);
      wc.removeListener("destroyed", cancel);
      return true;
    };
    function cancel() {
      if (finish()) helper.kill();
    }
    active.set(wc.id, cancel);
    wc.once("destroyed", cancel);
    // Without the script no release can be seen out here, so stop following;
    // the page still ends the drag from its own events.
    helper.once("error", finish);
    helper.once("exit", (code) => {
      if (finish() && code === 0) wc.send("pointer-follow:released");
    });
  }

  ipcMain.on("pointer-follow:start", (event, held) => {
    if (platform === "darwin" && FOLLOWED_BUTTONS.has(held)) start(event.sender, held);
  });
  ipcMain.on("pointer-follow:stop", (event) => {
    active.get(event.sender.id)?.();
  });
}

module.exports = { createPointerFollow };
