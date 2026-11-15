// Websites in web tabs see what this shell is: Chromium in Electron. Only the
// token Electron adds for the App itself (its name and version before
// "Chrome/", such as "openprogram-desktop/x" or "OpenProgramDev/x") is
// dropped. "Electron/x" stays, with the full Chrome version: the page's
// navigator.userAgentData names Chromium, not Google Chrome, and bot defenses
// such as Cloudflare treat a User-Agent that claims plain Chrome without
// Chrome's brands as forged and keep the visitor in their challenge.
function browserUserAgent(userAgent) {
  return String(userAgent || "")
    .replace(/(\(KHTML, like Gecko\))(?:\s+(?!(?:Chrome|Electron)\/)\S+\/\S+)+(?=\s+Chrome\/)/, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function useBrowserUserAgent(targetSession) {
  const userAgent = browserUserAgent(targetSession.getUserAgent());
  if (userAgent) targetSession.setUserAgent(userAgent);
  return userAgent;
}

// Cloudflare's clearance cookies hold for the User-Agent that earned them. When
// the web tab User-Agent changes (a new Electron, or a change here), the old
// ones keep the site in its challenge, so they go; every other cookie stays.
const CLEARANCE_COOKIE = /^(?:cf_clearance|__cf_bm|cf_chl_\w*)$/;

async function forgetClearanceOnChange(targetSession, filePath, userAgent) {
  const fs = require("fs");
  let previous = null;
  try {
    previous = JSON.parse(fs.readFileSync(filePath, "utf8")).userAgent || null;
  } catch (_error) {
    // First run with this file: the cookies may predate any recorded agent.
  }
  if (previous === userAgent) return 0;
  let removed = 0;
  for (const cookie of await targetSession.cookies.get({})) {
    if (!CLEARANCE_COOKIE.test(cookie.name)) continue;
    const host = String(cookie.domain || "").replace(/^\./, "");
    if (!host) continue;
    try {
      await targetSession.cookies.remove(`https://${host}${cookie.path || "/"}`, cookie.name);
      removed += 1;
    } catch (_error) {
      // A cookie that cannot be removed only keeps its old challenge.
    }
  }
  try {
    fs.writeFileSync(filePath, JSON.stringify({ userAgent }));
  } catch (_error) {
    // The next launch clears again.
  }
  return removed;
}

module.exports = { browserUserAgent, useBrowserUserAgent, forgetClearanceOnChange, CLEARANCE_COOKIE };
