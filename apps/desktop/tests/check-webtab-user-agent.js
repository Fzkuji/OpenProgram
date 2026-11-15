const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { browserUserAgent, useBrowserUserAgent, forgetClearanceOnChange } = require("../main/web-views/webtab-user-agent");

const electron = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
  + "(KHTML, like Gecko) openprogram-desktop/0.9.8 Chrome/156.0.8078.75 "
  + "Electron/45.2.1 Safari/537.36";
// Chromium in Electron, as it is: only the App's own token goes.
const browser = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
  + "(KHTML, like Gecko) Chrome/156.0.8078.75 Electron/45.2.1 Safari/537.36";

assert.equal(browserUserAgent(electron), browser);
assert.equal(browserUserAgent(browser), browser, "a UA without the App's token is left alone");
assert.match(browserUserAgent(electron), /Electron\/45\.2\.1/, "Electron stays");

const development = electron.replace("openprogram-desktop/0.9.8", "OpenProgramDev/0.9.8");
assert.equal(browserUserAgent(development), browser, "the development App's name is dropped too");

let set = null;
const session = { getUserAgent: () => electron, setUserAgent: (value) => { set = value; } };
assert.equal(useBrowserUserAgent(session), browser);
assert.equal(set, browser);

// Cloudflare clearance cookies go when the User-Agent changes; nothing else does.
(async () => {
  const jar = [
    { name: "cf_clearance", domain: ".vps.hosting", path: "/" },
    { name: "cf_chl_rc_ni", domain: "vps.hosting", path: "/" },
    { name: "__cf_bm", domain: ".example.com", path: "/" },
    { name: "session", domain: ".vps.hosting", path: "/" },
  ];
  const removed = [];
  const cookieSession = { cookies: {
    get: async () => jar,
    remove: async (url, name) => { removed.push(`${url} ${name}`); },
  } };
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "webtab-ua-")), "webtab-user-agent.json");
  assert.equal(await forgetClearanceOnChange(cookieSession, file, browser), 3, "first run clears");
  assert.deepEqual(removed, [
    "https://vps.hosting/ cf_clearance", "https://vps.hosting/ cf_chl_rc_ni", "https://example.com/ __cf_bm",
  ]);
  assert.equal(await forgetClearanceOnChange(cookieSession, file, browser), 0, "same agent keeps them");
  assert.equal(await forgetClearanceOnChange(cookieSession, file, browser.replace("45.2.1", "46.0.0")), 3,
    "a new agent clears again");
  console.log("webtab user agent checks passed");
})().catch((error) => { console.error(error); process.exit(1); });
