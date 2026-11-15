const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  isSecureUrl, lowEntropyHeaders, acceptedHints, hintHeaders, registerClientHints,
} = require("../main/web-views/webtab-client-hints");
const { registerWebTabPermissions } = require("../main/web-views/webtab-permissions");

(async () => {
  // Client hints carry what the session's own userAgentData reports.
  const data = {
    brands: [{ brand: "Not:A-Brand", version: "8" }, { brand: "Chromium", version: "156" }],
    mobile: false,
    platform: "macOS",
    architecture: "arm",
    bitness: "64",
    fullVersionList: [{ brand: "Not:A-Brand", version: "8.0.0.0" }, { brand: "Chromium", version: "156.0.8078.75" }],
    model: "",
    platformVersion: "15.7.3",
    uaFullVersion: "156.0.8078.75",
    wow64: false,
    formFactors: ["Desktop"],
  };
  assert.deepEqual(lowEntropyHeaders(data), {
    "sec-ch-ua": '"Not:A-Brand";v="8", "Chromium";v="156"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
  });
  assert.ok(isSecureUrl("https://example.com/"));
  assert.ok(isSecureUrl("http://localhost:3000/"));
  assert.ok(isSecureUrl("http://127.0.0.1/"));
  assert.ok(!isSecureUrl("http://example.com/"), "plain http gets no hints, as in Chrome");
  assert.deepEqual(hintHeaders("http://example.com/", data, new Map()), {});
  assert.deepEqual(acceptedHints("Sec-CH-UA-Full-Version-List, sec-ch-ua-arch, Sec-CH-Prefers-Color-Scheme"),
    ["sec-ch-ua-full-version-list", "sec-ch-ua-arch"]);
  const requested = new Map([["https://example.com", ["sec-ch-ua-full-version-list", "sec-ch-ua-platform-version"]]]);
  assert.deepEqual(hintHeaders("https://example.com/page", data, requested), {
    ...lowEntropyHeaders(data),
    "sec-ch-ua-full-version-list": '"Not:A-Brand";v="8.0.0.0", "Chromium";v="156.0.8078.75"',
    "sec-ch-ua-platform-version": '"15.7.3"',
  });
  assert.deepEqual(Object.keys(hintHeaders("https://other.example/", data, requested)).sort(),
    ["sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform"], "high-entropy hints stay with the origin that asked");

  // The registered listeners learn Accept-CH and add the headers.
  const listeners = {};
  const fakeSession = {
    webRequest: {
      onHeadersReceived: (fn) => { listeners.received = fn; },
      onBeforeSendHeaders: (fn) => { listeners.send = fn; },
    },
  };
  registerClientHints(fakeSession, async () => data);
  await new Promise((resolve) => setImmediate(resolve));
  const call = (fn, details) => new Promise((resolve) => fn(details, resolve));
  await call(listeners.received, { url: "https://site.example/", resourceType: "mainFrame", responseHeaders: { "Accept-CH": ["Sec-CH-UA-Arch"] } });
  const sent = await call(listeners.send, { url: "https://site.example/app.js", requestHeaders: { Accept: "*/*" } });
  assert.equal(sent.requestHeaders.Accept, "*/*");
  assert.equal(sent.requestHeaders["sec-ch-ua-arch"], '"arm"');
  assert.equal(sent.requestHeaders["sec-ch-ua-platform"], '"macOS"');
  assert.deepEqual(await call(listeners.send, { url: "http://site.example/", requestHeaders: {} }), {});

  // Permissions: harmless ones pass, devices ask once per origin, the rest is denied.
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-permissions-"));
  try {
    const handlers = {};
    const permissionSession = {
      setPermissionRequestHandler: (fn) => { handlers.request = fn; },
      setPermissionCheckHandler: (fn) => { handlers.check = fn; },
    };
    const asked = [];
    let answer = true;
    const decisions = registerWebTabPermissions(permissionSession, {
      filePath: path.join(dir, "decisions.json"),
      ask: async (question) => { asked.push(question.purpose); return answer; },
    });
    const request = (permission, details) => new Promise((resolve) =>
      handlers.request({ getURL: () => "https://cam.example/x" }, permission, resolve, details));
    assert.equal(await request("fullscreen", {}), true);
    assert.equal(await request("hid", {}), false, "unknown permissions are denied");
    assert.equal(handlers.check(null, "media", "https://cam.example", { mediaType: "video" }), false);
    assert.equal(await request("media", { requestingUrl: "https://cam.example/x", mediaTypes: ["video"] }), true);
    assert.deepEqual(asked, ["use your camera"]);
    assert.equal(await request("media", { requestingUrl: "https://cam.example/y", mediaTypes: ["video"] }), true);
    assert.equal(asked.length, 1, "an answer is remembered");
    assert.equal(handlers.check(null, "media", "https://cam.example", { mediaType: "video" }), true);
    assert.equal(handlers.check(null, "media", "https://cam.example", { mediaType: "audio" }), false,
      "allowing the camera does not allow the microphone");
    answer = false;
    assert.equal(await request("geolocation", { requestingUrl: "https://cam.example/" }), false);
    assert.equal(await request("geolocation", { requestingUrl: "https://cam.example/" }), false);
    assert.equal(asked.length, 2, "a block is remembered too");
    const saved = JSON.parse(fs.readFileSync(path.join(dir, "decisions.json"), "utf8"));
    assert.deepEqual(saved["https://cam.example"], { "media:video": "allow", geolocation: "block" });
    decisions.clear();
    assert.equal(handlers.check(null, "media", "https://cam.example", { mediaType: "video" }), false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
  console.log("webtab identity checks passed");
})().catch((error) => { console.error(error); process.exit(1); });
