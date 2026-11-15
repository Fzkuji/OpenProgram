// Response renderer excerpt from @playwright/mcp 0.0.79 / playwright-core
// Copyright Microsoft Corporation. Apache-2.0. Kept verbatim for the empty-tab regression.
function renderTabMarkdown(tab2) {
  const lines = [`- Page URL: ${tab2.url}`];
  if (tab2.title)
    lines.push(`- Page Title: ${tab2.title}`);
  if (tab2.crashed)
    lines.push(`- Page status: crashed`);
  const status = tab2.mainDocumentStatus;
  if (status && (status.status < 200 || status.status >= 300))
    lines.push(`- HTTP status: ${status.status}${status.statusText ? " " + status.statusText : ""}`);
  if (tab2.console.errors || tab2.console.warnings)
    lines.push(`- Console: ${tab2.console.errors} errors, ${tab2.console.warnings} warnings`);
  return lines;
}
function renderTabsMarkdown(tabs) {
  if (!tabs.length)
    return ["No open tabs. Navigate to a URL to create one."];
  const lines = [];
  for (let i = 0; i < tabs.length; i++) {
    const tab2 = tabs[i];
    const current = tab2.current ? " (current)" : "";
    const crashed = tab2.crashed ? " [crashed]" : "";
    lines.push(`- ${i}:${current} [${tab2.title}](${tab2.url})${crashed}`);
  }
  return lines;
}

async function build() {
        const sections = [];
        const addSection = (title, content, codeframe) => {
          const section = { title, content, isError: title === "Error", codeframe };
          sections.push(section);
          return content;
        };
        if (this._errors.length)
          addSection("Error", this._errors);
        if (this._results.length)
          addSection("Result", this._results);
        const codegen2 = this._context.config.codegen ?? "typescript";
        if (codegen2 !== "none" && this._code.length) {
          const code = substituteSecrets(renderCode(this._code, codegen2), codegen2, Object.keys(this._context.config.secrets ?? {}));
          addSection("Ran Playwright code", code, codegen2 === "typescript" ? "js" : codegen2);
        }
        const snapshotToFile = this._includeSnapshot !== "explicit" || !!this._includeSnapshotFileName;
        const ariaFormat = this._includeSnapshot === "none" ? "none" : this._json && !snapshotToFile ? "json" : "text";
        const tabSnapshot = this._context.currentTab() ? await this._context.currentTabOrDie().captureSnapshot(this._includeSnapshotRoot, this._includeSnapshotDepth, this._includeSnapshotBoxes, this._clientWorkspace, ariaFormat) : void 0;
        const tabHeaders = await Promise.all(this._context.tabs().map((tab2) => tab2.headerSnapshot()));
        if (this._includeSnapshot !== "none" || tabHeaders.some((header) => header.changed)) {
          if (tabHeaders.length !== 1)
            addSection("Open tabs", renderTabsMarkdown(tabHeaders));
          addSection("Page", renderTabMarkdown(tabHeaders.find((h) => h.current) ?? tabHeaders[0]));
        }
        if (tabSnapshot?.modalStates.length)
          addSection("Modal state", renderModalStates(this._context.config, tabSnapshot.modalStates));
        if (tabSnapshot && this._includeSnapshot !== "none") {
          if (snapshotToFile) {
            const suggestedFilename = this._includeSnapshotFileName === "<auto>" ? void 0 : this._includeSnapshotFileName;
            const resolvedFile = await this.resolveClientFile({ prefix: "page", ext: "yml", suggestedFilename }, "Snapshot");
            await this._writeFile(resolvedFile, tabSnapshot.ariaSnapshot);
            addSection("Snapshot", [resolvedFile.printableLink]);
          } else if (tabSnapshot.ariaSnapshotJSON !== void 0) {
            addSection("Snapshot", { json: tabSnapshot.ariaSnapshotJSON }, "json");
          } else {
            addSection("Snapshot", [tabSnapshot.ariaSnapshot], "yaml");
          }
        }
        const text2 = [];
        if (tabSnapshot?.consoleLink)
          text2.push(`- New console entries: ${tabSnapshot.consoleLink}`);
        if (tabSnapshot?.events.filter((event) => event.type !== "request").length) {
          for (const event of tabSnapshot.events) {
            if (event.type === "download-start")
              text2.push(`- Downloading file ${event.download.download.suggestedFilename()} ...`);
            else if (event.type === "download-finish")
              text2.push(`- Downloaded file ${event.download.download.suggestedFilename()} to "${this._computeRelativeTo(event.download.outputFile)}"`);
          }
        }
        if (text2.length)
          addSection("Events", text2);
        const pausedDetails = this._context.debugger().pausedDetails();
        if (pausedDetails) {
          addSection("Paused", [
            `- ${pausedDetails.title} at ${this._computeRelativeTo(pausedDetails.location.file)}${pausedDetails.location.line ? ":" + pausedDetails.location.line : ""}`,
            "- Use any tools to explore and interact, resume by calling resume/step-over/pause-at"
          ]);
        }
        return sections;
      }
module.exports = build;
