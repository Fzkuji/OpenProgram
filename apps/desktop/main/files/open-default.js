"use strict";
// "Open in default app" for a file the chat names: the system opens it as
// a double-click in Finder or Explorer would. Only a regular file is
// opened, and never one the system would run instead of open (a program,
// a script, an app bundle, a shortcut), so the menu item cannot be used
// to start something the agent wrote.

const RUNNABLE = new Set([
  ".app", ".appimage", ".bat", ".bash", ".cmd", ".com", ".command", ".cpl", ".csh", ".desktop",
  ".dmg", ".exe", ".fileloc", ".hta", ".inetloc", ".jar", ".js", ".jse", ".ksh", ".lnk", ".msc",
  ".msi", ".msp", ".pif", ".pkg", ".pl", ".ps1", ".py", ".pyw", ".rb", ".reg", ".scpt", ".scr",
  ".sh", ".terminal", ".tool", ".url", ".vb", ".vbe", ".vbs", ".webloc", ".workflow", ".ws",
  ".wsf", ".zsh",
]);

function openableFile(filePath, { fs, path, platform }) {
  if (typeof filePath !== "string" || !filePath || filePath.includes("\0")) return false;
  if (!path.isAbsolute(filePath)) return false;
  if (RUNNABLE.has(path.extname(filePath).toLowerCase())) return false;
  let stat;
  try { stat = fs.statSync(filePath); } catch { return false; }
  if (!stat.isFile()) return false;
  return platform === "win32" || (stat.mode & 0o111) === 0;
}

function registerOpenFileIpc({ ipcMain, shell, fs, path, platform, contextForSender }) {
  ipcMain.handle("files:open-default", async (event, filePath) => {
    const ctx = contextForSender(event);
    // Only a window's own page, never a website shown in a tab.
    if (!ctx || event.sender !== ctx.win.webContents) return false;
    if (!openableFile(filePath, { fs, path, platform })) return false;
    return (await shell.openPath(filePath)) === "";
  });
}

module.exports = { openableFile, registerOpenFileIpc, RUNNABLE };
