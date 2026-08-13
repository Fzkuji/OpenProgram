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
  const fakeBin = path.join(temp, "openprogram");
  const token = "A".repeat(43);
  fs.writeFileSync(
    fakeBin,
    `#!/bin/sh\n` +
      `test "$1" = "web" && test "$2" = "auth-url" || exit 2\n` +
      `test "$3" = "--base-url" && test "$4" = "http://127.0.0.1:18100" || exit 3\n` +
      `printf 'http://localhost:18100/#token=${token}\\n'\n`,
    { mode: 0o700 },
  );

  const actual = resolveAuthenticatedStartUrl(
    "http://127.0.0.1:18100/chat?profile=worker",
    { ...process.env, PATH: `${temp}${path.delimiter}${process.env.PATH || ""}` },
  );
  assert.equal(
    actual,
    `http://localhost:18100/chat?profile=worker#token=${token}`,
    "desktop must preserve its route while bootstrapping the active worker token",
  );
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}

console.log("worker start URL check passed");
