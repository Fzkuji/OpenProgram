"use strict";

const WINDOWS_TITLEBAR_HEIGHT = 40;
const MACOS_BUTTON_FRAME_HEIGHT = 16;

function macOSButtonPosition(zoom = 1) {
  const inset = Math.max(0, Math.round((WINDOWS_TITLEBAR_HEIGHT * zoom - MACOS_BUTTON_FRAME_HEIGHT) / 2));
  return {
    // The native circle's vertical frame inset is 1 DIP larger than its
    // horizontal inset. Compensate so its visible top and left margins match.
    x: inset + 1,
    y: inset,
  };
}

/** Native-window options that make the product tab strip the title bar.
 *
 * Windows uses Electron's Window Controls Overlay instead of a fully
 * frameless window. That keeps Snap Layouts, resize borders, DWM shadow and
 * accessibility while placing the caption buttons inside our 40px tab row.
 */
function browserWindowChromeOptions(platform, chrome) {
  if (platform === "darwin") {
    return {
      titleBarStyle: "hiddenInset",
      trafficLightPosition: macOSButtonPosition(),
    };
  }
  if (platform === "win32") {
    return {
      titleBarStyle: "hidden",
      titleBarOverlay: {
        color: "#00000000",
        symbolColor: chrome.text,
        height: WINDOWS_TITLEBAR_HEIGHT,
      },
      autoHideMenuBar: true,
    };
  }
  return { autoHideMenuBar: true };
}

function applyNativeTitleBarChrome(win, platform, chrome) {
  if (!win || win.isDestroyed?.()) return;
  if (platform === "darwin") {
    if (win.webContents?.isDestroyed?.()) return;
    const currentZoom = Number(win.webContents?.getZoomFactor?.());
    const zoom = Number.isFinite(currentZoom) && currentZoom > 0 ? currentZoom : 1;
    win.setWindowButtonPosition?.(macOSButtonPosition(zoom));
  }
  if (platform === "win32") {
    // Keep the application menu for keyboard accelerators, but never spend a
    // permanent second row on File/Edit/View/Window.
    win.setMenuBarVisibility?.(false);
    win.setTitleBarOverlay?.({
      color: "#00000000",
      symbolColor: chrome.text,
      height: WINDOWS_TITLEBAR_HEIGHT,
    });
  }
  if (platform === "linux") {
    // Accelerators keep working from the hidden application menu.
    win.setMenuBarVisibility?.(false);
  }
}

function registerNativeTitleBarIpc({ ipcMain, BrowserWindow, platform, getChrome }) {
  if (platform !== "darwin") return;
  ipcMain.on("window:sync-chrome", (event) => {
    const win = BrowserWindow.fromWebContents(event.sender);
    // Overlay views and subframes must not reposition their host window.
    if (!win || win.webContents !== event.sender || event.senderFrame !== event.sender.mainFrame) return;
    applyNativeTitleBarChrome(win, platform, getChrome());
  });
}

module.exports = {
  WINDOWS_TITLEBAR_HEIGHT,
  browserWindowChromeOptions,
  applyNativeTitleBarChrome,
  registerNativeTitleBarIpc,
};
