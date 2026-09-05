const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const helperPath = path.join(__dirname, "..", "worker-start-url.js");
assert.ok(
  fs.existsSync(helperPath),
  "desktop worker-start URL helper is missing",
);

const { resolveAuthenticatedStartUrl } = require(helperPath);

const temp = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-desktop-auth-"));
try {
  const token = "A".repeat(43);
  const fakeBin = path.join(temp, "openprogram");
  const windowsAuthHelper = path.join(temp, "auth-helper.js");
  if (process.platform === "win32") {
    fs.writeFileSync(
      windowsAuthHelper,
      `const a = process.argv.slice(2);\n` +
        `if (JSON.stringify(a) !== JSON.stringify(["web", "auth-url", "--base-url", "http://127.0.0.1:18100"])) process.exit(2);\n` +
        `process.stdout.write("http://localhost:18100/#token=${token}\\n");\n`,
    );
  } else {
    fs.writeFileSync(
      fakeBin,
      `#!/bin/sh\n` +
        `test "$1" = "web" && test "$2" = "auth-url" || exit 2\n` +
        `test "$3" = "--base-url" && test "$4" = "http://127.0.0.1:18100" || exit 3\n` +
        `printf 'http://localhost:18100/#token=${token}\\n'\n`,
      { mode: 0o700 },
    );
  }

  const actual = resolveAuthenticatedStartUrl(
    "http://127.0.0.1:18100/chat?profile=worker",
    { ...process.env, PATH: `${temp}${path.delimiter}${process.env.PATH || ""}` },
    process.platform === "win32"
      ? [{ command: process.execPath, args: [windowsAuthHelper] }]
      : undefined,
  );
  assert.equal(
    actual,
    `http://localhost:18100/chat?profile=worker#token=${token}`,
    "desktop must preserve its route while bootstrapping the active worker token",
  );

  const embeddedPython = process.platform === "win32"
    ? process.execPath
    : path.join(temp, "embedded-python");
  const embeddedPrefix = [];
  if (process.platform === "win32") {
    const helper = path.join(temp, "embedded-auth-helper.js");
    fs.writeFileSync(
      helper,
      `const a = process.argv.slice(2);\n` +
        `if (JSON.stringify(a) !== JSON.stringify(["-I", "-B", "-m", "openprogram", "web", "auth-url", "--base-url", "http://127.0.0.1:18100"])) process.exit(2);\n` +
        `process.stdout.write("http://127.0.0.1:18100/#token=${token}\\n");\n`,
    );
    embeddedPrefix.push(helper);
  } else {
    fs.writeFileSync(
      embeddedPython,
      `#!/bin/sh\n` +
        `test "$1" = "-I" && test "$2" = "-B" || exit 2\n` +
        `test "$3" = "-m" && test "$4" = "openprogram" || exit 3\n` +
        `test "$5" = "web" && test "$6" = "auth-url" || exit 4\n` +
        `test "$7" = "--base-url" && test "$8" = "http://127.0.0.1:18100" || exit 5\n` +
        `printf 'http://127.0.0.1:18100/#token=${token}\n'\n`,
      { mode: 0o700 },
    );
  }
  const packaged = resolveAuthenticatedStartUrl(
    "http://127.0.0.1:18100/settings/general",
    { ...process.env, PATH: "/usr/bin:/bin" },
    [{
      command: embeddedPython,
      args: [...embeddedPrefix, "-I", "-B", "-m", "openprogram"],
    }],
  );
  assert.equal(
    packaged,
    `http://127.0.0.1:18100/settings/general#token=${token}`,
    "packaged desktop must bootstrap auth with its embedded Python",
  );
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}

console.log("worker start URL check passed");
