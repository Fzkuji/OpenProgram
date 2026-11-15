// User-Agent Client Hints for web tabs. Chrome sends `Sec-CH-UA`,
// `Sec-CH-UA-Mobile` and `Sec-CH-UA-Platform` with every request to a secure
// origin, and the high-entropy hints a site names in `Accept-CH`. Electron
// sends none of them, yet its pages expose the same values to script through
// `navigator.userAgentData`; a browser whose headers and script disagree
// looks forged. These headers carry exactly what this Chromium reports to
// script, read from a page in the web tab session itself, so nothing claims
// to be another browser.

// Header name → how to write it from getHighEntropyValues().
const brandList = (brands) => brands.map(({ brand, version }) => `"${brand}";v="${version}"`).join(", ");
const quoted = (value) => `"${String(value ?? "")}"`;
const HIGH_ENTROPY = {
  "sec-ch-ua-arch": (data) => quoted(data.architecture),
  "sec-ch-ua-bitness": (data) => quoted(data.bitness),
  "sec-ch-ua-full-version": (data) => quoted(data.uaFullVersion),
  "sec-ch-ua-full-version-list": (data) => brandList(data.fullVersionList || []),
  "sec-ch-ua-model": (data) => quoted(data.model),
  "sec-ch-ua-platform-version": (data) => quoted(data.platformVersion),
  "sec-ch-ua-wow64": (data) => (data.wow64 ? "?1" : "?0"),
  "sec-ch-ua-form-factors": (data) => (data.formFactors || []).map(quoted).join(", "),
};
const HINTS_SCRIPT = `navigator.userAgentData.getHighEntropyValues(${JSON.stringify([
  "architecture", "bitness", "fullVersionList", "model", "platformVersion",
  "uaFullVersion", "wow64", "formFactors",
])})`;

// Chrome sends hints only to potentially trustworthy origins.
function isSecureUrl(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:" || parsed.protocol === "wss:") return true;
    if (parsed.protocol !== "http:" && parsed.protocol !== "ws:") return false;
    const host = parsed.hostname;
    return host === "localhost" || host.endsWith(".localhost") || host === "127.0.0.1" || host === "[::1]";
  } catch (_error) {
    return false;
  }
}

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch (_error) {
    return null;
  }
}

function lowEntropyHeaders(data) {
  return {
    "sec-ch-ua": brandList(data.brands || []),
    "sec-ch-ua-mobile": data.mobile ? "?1" : "?0",
    "sec-ch-ua-platform": quoted(data.platform),
  };
}

function acceptedHints(value) {
  return String(value || "")
    .split(",")
    .map((name) => name.trim().toLowerCase())
    .filter((name) => name in HIGH_ENTROPY);
}

function headerValue(headers, name) {
  const key = Object.keys(headers || {}).find((candidate) => candidate.toLowerCase() === name);
  const value = key ? headers[key] : undefined;
  return Array.isArray(value) ? value.join(", ") : value;
}

/** The hint headers one request carries, given what its origin asked for. */
function hintHeaders(url, data, requested) {
  if (!data || !isSecureUrl(url)) return {};
  const headers = lowEntropyHeaders(data);
  for (const name of requested?.get(originOf(url)) || []) headers[name] = HIGH_ENTROPY[name](data);
  return headers;
}

/**
 * Read the session's own userAgentData once, then add the hint headers to
 * every web tab request. `readHints()` resolves the getHighEntropyValues()
 * result of a page in the session; requests before it resolves go without
 * hints, as they did before.
 */
function registerClientHints(targetSession, readHints) {
  let data = null;
  // Origin → high-entropy hints it asked for; kept for this run, as Chrome
  // keeps them for the browsing session.
  const requested = new Map();
  Promise.resolve()
    .then(readHints)
    .then((value) => {
      if (value && Array.isArray(value.brands)) data = value;
    })
    .catch(() => {});

  targetSession.webRequest.onHeadersReceived((details, callback) => {
    if (details.resourceType === "mainFrame" || details.resourceType === "subFrame") {
      const origin = originOf(details.url);
      const accept = headerValue(details.responseHeaders, "accept-ch");
      if (origin && accept !== undefined) requested.set(origin, acceptedHints(accept));
    }
    callback({});
  });
  targetSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const added = hintHeaders(details.url, data, requested);
    if (!Object.keys(added).length) return callback({});
    callback({ requestHeaders: { ...details.requestHeaders, ...added } });
  });
}

/**
 * Read getHighEntropyValues() from a hidden page in `targetSession`.
 * userAgentData exists only in secure contexts, so the page comes from a
 * loopback server that lives for this one read.
 */
async function readClientHints(targetSession) {
  const { WebContentsView } = require("electron");
  const http = require("http");
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" });
    response.end("<!doctype html><title></title>");
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  // A view in no window: closing it never counts as the last window closing.
  const probe = new WebContentsView({ webPreferences: { session: targetSession } }).webContents;
  try {
    await probe.loadURL(`http://127.0.0.1:${server.address().port}/`);
    return await probe.executeJavaScript(HINTS_SCRIPT);
  } finally {
    probe.close();
    server.close();
  }
}

module.exports = {
  readClientHints,
  HINTS_SCRIPT,
  isSecureUrl,
  lowEntropyHeaders,
  acceptedHints,
  hintHeaders,
  registerClientHints,
};
