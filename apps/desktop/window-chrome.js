"use strict";

const WINDOWS_TITLEBAR_HEIGHT = 40;

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
      trafficLightPosition: { x: 18, y: 13 },
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
  return {};
}

function applyNativeTitleBarChrome(win, platform, chrome) {
  if (!win || win.isDestroyed?.()) return;
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
}

module.exports = {
  WINDOWS_TITLEBAR_HEIGHT,
  browserWindowChromeOptions,
  applyNativeTitleBarChrome,
};
