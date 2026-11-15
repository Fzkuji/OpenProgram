// Loaded into the Playwright MCP process that drives the user's own Chrome or
// Edge. Playwright brings a tab to the front whenever the Agent switches to
// it, and opens new tabs in front; neither has an option, and both pull the
// page away from a person who is using the browser. Once connected with a
// token, the extension also focuses the browser's window; its own
// background option turns that off. The browser coming in front of the other
// applications when the connect page opens is Chrome's doing and is undone
// by computer_use_agent/desktop/front.py.
"use strict";

const Module = require("module");
// Playwright 0.0.79 can lose the last attached tab while awaiting a
// snapshot. Its response renderer then dereferences an absent header's url.
const PAGE_HEADER = 'addSection("Page", renderTabMarkdown(tabHeaders.find((h) => h.current) ?? tabHeaders[0]));';
const EDITS = [
  [PAGE_HEADER, 'if (tabHeaders.length) ' + PAGE_HEADER],
  ['url3.searchParams.set("token", token);',
    '{ url3.searchParams.set("token", token); url3.searchParams.set("background", "true"); }'],
  ['"chrome.tabs.create", [{ url: url3 }]', '"chrome.tabs.create", [{ url: url3, active: false }]'],
  ['throw new Error(`Tab ${index} not found`);\n        await tab2.page.bringToFront();',
    "throw new Error(`Tab ${index} not found`);"],
];

const compile = Module.prototype._compile;
Module.prototype._compile = function (content, filename) {
  if (/playwright-core[\\/]lib[\\/]coreBundle\.js$/.test(filename)) {
    for (const [from, to] of EDITS) {
      if (content.includes(from)) content = content.replace(from, to);
      else if (from === PAGE_HEADER) throw new Error("Unsupported Playwright response renderer: empty-tab guard could not be installed");
      else process.stderr.write(`playwright_background_tabs: not found in this Playwright: ${from}\n`);
    }
  }
  return compile.call(this, content, filename);
};
