// Only the trusted UI main frame can write. Web tabs cannot access this IPC.
function registerClipboardIpc(ipcMain, clipboard, ownerOf) {
  ipcMain.handle("clipboard:write-text", (event, text) => {
    if (!ownerOf(event)) throw new Error("Unauthorized clipboard sender");
    if (typeof text !== "string" || text.length > 1_000_000) throw new Error("Invalid clipboard text");
    clipboard.writeText(text);
  });
}
module.exports = { registerClipboardIpc };
