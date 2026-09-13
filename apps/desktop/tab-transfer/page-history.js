// Page snapshots travel with their owning tab; nested histories are rejected.
function copyNavigationFields(tab, normalized, boundedString) {
  if (tab.navigationRoute !== undefined) {
    boundedString(tab.navigationRoute, "navigationRoute", 16 * 1024, true);
    if (!tab.navigationRoute.startsWith("/") || tab.navigationRoute.startsWith("//")
      || /[\\\r\n]/.test(tab.navigationRoute)) throw new TypeError("Invalid navigation route");
    normalized.navigationRoute = tab.navigationRoute;
  }
  const snapshot = tab.fileNavigationSnapshot;
  if (snapshot !== undefined) {
    if (!snapshot || !["file", "dir"].includes(snapshot.selectedType)
      || !Array.isArray(snapshot.expanded)) throw new TypeError("Invalid file navigation snapshot");
    for (const field of ["projectId", "path"]) boundedString(snapshot[field], field, 16 * 1024);
    for (const path of snapshot.expanded) boundedString(path, "expanded path", 16 * 1024);
    if (snapshot.scroll !== null && (!snapshot.scroll || typeof snapshot.scroll.path !== "string"
      || !Number.isFinite(snapshot.scroll.offset))) throw new TypeError("Invalid file navigation scroll");
    normalized.fileNavigationSnapshot = {
      projectId: snapshot.projectId, path: snapshot.path, selectedType: snapshot.selectedType,
      expanded: [...snapshot.expanded], scroll: snapshot.scroll && { ...snapshot.scroll },
    };
  }
}

function copyPageHistory(tab, normalized, normalizePage) {
  const history = tab.pageHistory;
  if (history === undefined) return;
  if (!history || !Array.isArray(history.entries) || !history.entries.length
    || !Number.isInteger(history.index) || history.index < 0 || history.index >= history.entries.length) {
    throw new TypeError("Invalid page history");
  }
  const entries = history.entries.map(page => {
    if (!page || typeof page !== "object" || "pageHistory" in page) throw new TypeError("Nested page history");
    const { sessionHistory: _legacy, ...snapshot } = page;
    return normalizePage(snapshot);
  });
  const current = entries[history.index];
  if (current.id !== tab.id || current.kind !== tab.kind || current.sessionId !== tab.sessionId
    || current.navigationRoute !== tab.navigationRoute) throw new TypeError("Page history does not match current tab");
  normalized.pageHistory = { entries, index: history.index };
}

module.exports = { copyNavigationFields, copyPageHistory };
