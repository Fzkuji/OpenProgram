"use strict";

const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const {
  killWindowsProcessTree,
  resolveTerminalCommand,
  waitForTerminalPid,
} = require("../main/terminal/terminal-command");
const { readWindowsUpdateLine, verifyWindowsAuthenticode } = require("../main/self-update/windows-signature");
const {
  browserWindowChromeOptions,
  applyNativeTitleBarChrome,
} = require("../main/windows/window-chrome");

const chrome = { text: "#b8b5ad" };
assert.deepEqual(browserWindowChromeOptions("win32", chrome), {
  titleBarStyle: "hidden",
  titleBarOverlay: {
    color: "#00000000",
    symbolColor: "#b8b5ad",
    height: 40,
  },
  autoHideMenuBar: true,
});
assert.deepEqual(browserWindowChromeOptions("darwin", chrome), {
  titleBarStyle: "hiddenInset",
  trafficLightPosition: { x: 13, y: 12 },
});
assert.deepEqual(browserWindowChromeOptions("linux", chrome), { autoHideMenuBar: true });

const chromeCalls = [];
applyNativeTitleBarChrome({
  isDestroyed: () => false,
  setMenuBarVisibility: (visible) => chromeCalls.push(["menu", visible]),
  setTitleBarOverlay: (options) => chromeCalls.push(["overlay", options]),
}, "win32", chrome);
assert.deepEqual(chromeCalls, [
  ["menu", false],
  ["overlay", {
    color: "#00000000",
    symbolColor: "#b8b5ad",
    height: 40,
  }],
]);

const desktopRoot = path.resolve(__dirname, "..");
const mainSource = require("../scripts/main-source").readMainSource();
const preloadSource = fs.readFileSync(path.join(desktopRoot, "main", "preload.js"), "utf8");
const chromeCss = fs.readFileSync(
  path.resolve(desktopRoot, "..", "web", "app", "styles", "base", "desktop-chrome.css"),
  "utf8",
);
const packageConfig = JSON.parse(
  fs.readFileSync(path.join(desktopRoot, "package.json"), "utf8"),
);
const installerSource = fs.readFileSync(
  path.join(desktopRoot, "build", "installer.nsh"),
  "utf8",
);
const upgradePrepSource = fs.readFileSync(
  path.join(desktopRoot, "build", "prepare-upgrade.ps1"),
  "utf8",
);
const packagedRuntimeSource = fs.readFileSync(
  path.join(desktopRoot, "main", "self-update", "packaged-runtime.js"),
  "utf8",
);
const workerStartUrlSource = fs.readFileSync(
  path.join(desktopRoot, "main", "worker", "worker-start-url.js"),
  "utf8",
);
assert.match(mainSource, /browserWindowChromeOptions\(process\.platform, currentChrome\)/);
assert.match(mainSource, /applyNativeTitleBarChrome\(win, process\.platform, currentChrome\)/);
assert.match(
  mainSource,
  /spawn\(launch\.command, launch\.args,[\s\S]*windowsHide: process\.platform === "win32"/,
);
assert.match(preloadSource, /platform: process\.platform/);
assert.match(chromeCss, /\.desktop-windows \.desktop-tab-row \{[\s\S]*padding-right: 138px;/);
assert.equal(packageConfig.build.nsis.include, "installer.nsh");
assert.match(installerSource, /!macro customInit/);
assert.match(installerSource, /prepare-upgrade\.ps1/);
assert.match(installerSource, /SetErrorLevel 12/);
// A fresh install runs no PowerShell before it installs, and no hook is a
// script file, which a Group Policy execution policy would refuse.
assert.match(
  installerSource,
  /!macro customInit\s+\$\{If\} \$\{FileExists\} "\$INSTDIR\\OpenProgram\.exe"\s+\$\{AndIf\} \$\{FileExists\} "\$INSTDIR\\resources\\app\.asar"[\s\S]*openProgramHook prepare-upgrade\.ps1[\s\S]*\$\{EndIf\}\s+!macroend/,
);
assert.doesNotMatch(installerSource.replace(/^\s*;.*$/gm, ""), /-File|-ExecutionPolicy/);
assert.doesNotMatch(installerSource, /customCheckAppRunning/);
assert.match(upgradePrepSource, /Get-CimInstance Win32_Process/);
assert.match(upgradePrepSource, /ConvertTo-ExtendedPath/);
assert.match(upgradePrepSource, /\[IO\.Directory\]::EnumerateFileSystemEntries/);
assert.match(upgradePrepSource, /\$Entry\.Length - \$ExtendedRoot\.Length\) -ge 248/);
assert.match(upgradePrepSource, /OpenProgram\.exe/);
assert.match(packagedRuntimeSource, /platform === "win32"/);
assert.match(packagedRuntimeSource, /pythonw\.exe/);
assert.match(packagedRuntimeSource, /authCommand: python/);
assert.match(workerStartUrlSource, /windowsHide: process\.platform === "win32"/);

const powershell = path.win32.join(
  "C:\\Windows",
  "System32",
  "WindowsPowerShell",
  "v1.0",
  "powershell.exe",
);
const shell = resolveTerminalCommand("shell", {
  platform: "win32",
  env: { SystemRoot: "C:\\Windows" },
  existsSync: (candidate) => candidate === powershell,
});
assert.deepEqual(shell, {
  command: powershell,
  args: ["-NoLogo"],
  useConpty: true,
});
const claude = resolveTerminalCommand("claude", {
  platform: "win32",
  env: { SystemRoot: "C:\\Windows" },
  existsSync: (candidate) => candidate === powershell,
});
assert.deepEqual(claude.args, ["-NoLogo", "-NoExit", "-Command", "claude"]);

const cmd = resolveTerminalCommand("claude", {
  platform: "win32",
  env: { SystemRoot: "D:\\Missing", ComSpec: "C:\\Windows\\System32\\cmd.exe" },
  existsSync: (candidate) => candidate.endsWith("cmd.exe"),
});
assert.deepEqual(cmd.args, ["/d", "/k", "claude"]);
assert.throws(() => resolveTerminalCommand("arbitrary"), /unsupported terminal preset/);
for (const [platform, available, expected] of [
  ["linux", ["/bin/bash", "/bin/sh"], "/bin/bash"],
  ["linux", ["/bin/sh"], "/bin/sh"],
  ["darwin", ["/bin/zsh", "/bin/sh"], "/bin/zsh"],
]) {
  assert.equal(resolveTerminalCommand("shell", {
    platform, env: {}, existsSync: (candidate) => available.includes(candidate),
  }).command, expected);
}
assert.throws(() => resolveTerminalCommand("shell", {
  platform: "linux", env: {}, existsSync: () => false,
}), /No usable terminal shell/);

let killInvocation = null;
const killed = killWindowsProcessTree(4242, {
  platform: "win32",
  env: { SystemRoot: "D:\\Windows" },
  spawnImpl: (command, args, options) => {
    killInvocation = { command, args, options };
    return { on() {}, unref() {} };
  },
});
assert.equal(killed, true);
assert.deepEqual(killInvocation, {
  command: "D:\\Windows\\System32\\taskkill.exe",
  args: ["/F", "/T", "/PID", "4242"],
  options: { windowsHide: true, stdio: "ignore" },
});
assert.equal(killWindowsProcessTree(0, { platform: "win32" }), false);
assert.equal(killWindowsProcessTree(4242, { platform: "linux" }), false);

const signature = verifyWindowsAuthenticode("C:\\Downloads\\OpenProgram.exe", {
  platform: "win32",
  existsSync: () => true,
  execFileSyncImpl: (_command, args, options) => {
    // powershell.exe would fold anything after -Command into the command text.
    assert.equal(args.at(-2), "-Command");
    assert.match(args.at(-1), /\$env:OPENPROGRAM_SIGNATURE_PATH/);
    assert.doesNotMatch(args.at(-1), /\$args/);
    assert.equal(options.env.OPENPROGRAM_SIGNATURE_PATH, "C:\\Downloads\\OpenProgram.exe");
    assert.equal(options.windowsHide, true);
    return JSON.stringify({
      status: "Valid",
      subject: "CN=OpenProgram",
      thumbprint: "A".repeat(40),
    });
  },
});
assert.deepEqual(signature, {
  subject: "CN=OpenProgram",
  thumbprint: "A".repeat(40),
});
assert.throws(
  () => verifyWindowsAuthenticode("C:\\Downloads\\OpenProgram.exe", {
    platform: "win32",
    existsSync: () => true,
    execFileSyncImpl: () => JSON.stringify({ status: "NotSigned" }),
  }),
  /not valid/,
);
assert.equal(
  verifyWindowsAuthenticode("/tmp/OpenProgram.dmg", { platform: "darwin" }),
  null,
);

function verifyOn(line, result) {
  return verifyWindowsAuthenticode("C:\\Downloads\\OpenProgram.exe", {
    platform: "win32",
    existsSync: () => true,
    execFileSyncImpl: () => JSON.stringify(result),
    line,
  });
}
const unsignedLine = { signed: false, subject: "", thumbprint: "" };
const signedLine = { signed: true, subject: "CN=OpenProgram", thumbprint: "A".repeat(40) };

assert.equal(verifyOn(unsignedLine, { status: "NotSigned" }).unsigned, true,
  "an App built unsigned takes an unsigned installer");
assert.equal(verifyOn(unsignedLine, { status: "Valid", subject: "CN=OpenProgram", thumbprint: "B".repeat(40) }).thumbprint,
  "B".repeat(40), "and may move up to a signed one");
for (const status of ["HashMismatch", "NotTrusted", "UnknownError"]) {
  assert.throws(() => verifyOn(unsignedLine, { status }), /not valid/, `the unsigned line still refuses ${status}`);
}
assert.throws(() => verifyOn(signedLine, { status: "NotSigned" }), /not valid/, "a signed App never moves to unsigned");
assert.equal(verifyOn(signedLine, { status: "Valid", subject: "CN=OpenProgram", thumbprint: "a".repeat(40) }).thumbprint,
  "A".repeat(40), "the same certificate passes");
assert.equal(verifyOn(signedLine, { status: "Valid", subject: "CN=OpenProgram", thumbprint: "C".repeat(40) }).thumbprint,
  "C".repeat(40), "a renewed certificate of the same publisher passes");
assert.throws(() => verifyOn(signedLine, { status: "Valid", subject: "CN=Someone Else", thumbprint: "C".repeat(40) }),
  /different publisher/);

// A line read without a publisher (PowerShell failed or timed out when the App
// read its own signature) must not let any validly signed installer through.
const unknownLine = { signed: true, subject: "", thumbprint: "" };
function verifyUnknown(installer, own) {
  const asked = [];
  const result = () => verifyWindowsAuthenticode("C:\\Downloads\\OpenProgram.exe", {
    platform: "win32",
    existsSync: () => true,
    executable: "C:\\App\\OpenProgram.exe",
    execFileSyncImpl: (_command, _args, options) => {
      const target = options.env.OPENPROGRAM_SIGNATURE_PATH;
      asked.push(target);
      if (target === "C:\\Downloads\\OpenProgram.exe") return JSON.stringify(installer);
      if (own instanceof Error) throw own;
      return JSON.stringify(own);
    },
    line: unknownLine,
  });
  return { result, asked };
}
const someoneElse = { status: "Valid", subject: "CN=Someone Else", thumbprint: "C".repeat(40) };
const ours = { status: "Valid", subject: "CN=OpenProgram", thumbprint: "A".repeat(40) };
assert.throws(verifyUnknown(someoneElse, new Error("PowerShell timed out")).result, /own publisher could not be read/,
  "an App that cannot read its own publisher takes no installer");
assert.throws(verifyUnknown(someoneElse, { status: "HashMismatch" }).result, /own publisher could not be read/,
  "nor does an App whose signature is damaged");
assert.throws(verifyUnknown(someoneElse, ours).result, /different publisher/,
  "the App's publisher is read again and another publisher is refused");
{
  const { result, asked } = verifyUnknown({ ...ours, thumbprint: "D".repeat(40) }, ours);
  assert.equal(result().thumbprint, "D".repeat(40), "the second read lets the App's own publisher through");
  assert.deepEqual(asked, ["C:\\Downloads\\OpenProgram.exe", "C:\\App\\OpenProgram.exe"]);
}
assert.throws(verifyUnknown({ status: "NotSigned" }, ours).result, /not valid/);

async function checkUpdateLine() {
  const line = (status, extra = {}) => readWindowsUpdateLine("C:\\App\\OpenProgram.exe", {
    platform: "win32",
    execFileImpl: async () => ({ stdout: JSON.stringify({ status, ...extra }) }),
  });
  assert.equal((await line("NotSigned")).signed, false);
  assert.deepEqual(await line("Valid", { subject: "CN=OpenProgram", thumbprint: "d".repeat(40) }), {
    signed: true, subject: "CN=OpenProgram", thumbprint: "D".repeat(40),
  });
  assert.equal((await line("HashMismatch")).signed, true, "a damaged signature takes the stricter line");
  assert.equal((await readWindowsUpdateLine("C:\\App\\OpenProgram.exe", {
    platform: "win32",
    execFileImpl: async () => { throw new Error("PowerShell is blocked"); },
  })).signed, true, "an unreadable signature takes the stricter line");
  assert.equal(await readWindowsUpdateLine("/tmp/OpenProgram", { platform: "linux" }), null);
}

// The real powershell.exe, which the checks above stand in for.
async function checkLiveSignatures() {
  if (process.platform !== "win32") return;
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "openprogram-signature-"));
  try {
    const compiled = path.join(directory, "compiled.exe");
    execFileSync("powershell.exe", [
      "-NoProfile", "-NonInteractive", "-Command",
      "Add-Type -TypeDefinition 'public class P { public static void Main() {} }' " +
        "-OutputType ConsoleApplication -OutputAssembly $env:UNSIGNED_OUTPUT",
    ], { env: { ...process.env, UNSIGNED_OUTPUT: compiled }, stdio: "pipe" });
    // A name with the characters a wildcard or a quoted command would trip on.
    const unsigned = path.join(directory, "open[program] it's.exe");
    fs.copyFileSync(compiled, unsigned);
    assert.equal((await readWindowsUpdateLine(unsigned)).signed, false);
    assert.equal(verifyWindowsAuthenticode(unsigned, { line: { signed: false } }).unsigned, true);
    assert.throws(() => verifyWindowsAuthenticode(unsigned, { line: { signed: true } }), /not valid/);

    const signed = path.join(process.env.SystemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
    const line = await readWindowsUpdateLine(signed);
    assert.equal(line.signed, true);
    assert.match(line.subject, /Microsoft/);
    assert.match(verifyWindowsAuthenticode(signed).subject, /Microsoft/);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

async function checkLiveConpty() {
  await checkUpdateLine();
  let reads = 0;
  const delayedTerminal = {
    get pid() {
      reads += 1;
      return reads >= 3 ? 4242 : 0;
    },
  };
  assert.equal(
    await waitForTerminalPid(delayedTerminal, {
      timeoutMs: 10,
      pollMs: 1,
      now: (() => {
        let tick = 0;
        return () => tick++;
      })(),
      delay: async () => {},
    }),
    4242,
  );
  assert.equal(
    await waitForTerminalPid({ pid: 0 }, {
      timeoutMs: 2,
      pollMs: 1,
      now: (() => {
        let tick = 0;
        return () => tick++;
      })(),
      delay: async () => {},
    }),
    null,
  );

  if (process.platform !== "win32") return;
  const nodePty = require("node-pty");
  const liveShell = resolveTerminalCommand("shell");
  const marker = "OPENPROGRAM_CONPTY_OK";
  let output = "";
  await new Promise((resolve, reject) => {
    const terminal = nodePty.spawn(
      liveShell.command,
      ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", `Write-Output ${marker}`],
      {
        cols: 80,
        rows: 24,
        cwd: process.cwd(),
        env: process.env,
        useConpty: true,
      },
    );
    const timer = setTimeout(() => {
      terminal.kill();
      reject(new Error("live ConPTY probe timed out"));
    }, 10_000);
    terminal.onData((data) => { output += data; });
    terminal.onExit(({ exitCode }) => {
      clearTimeout(timer);
      if (exitCode !== 0) {
        reject(new Error(`live ConPTY probe exited with ${exitCode}`));
        return;
      }
      resolve();
    });
  });
  assert.match(output, new RegExp(marker));
}

checkLiveSignatures().then(checkLiveConpty).then(() => {
  console.log("Windows Desktop checks passed");
  process.exit(0);
}).catch((error) => {
  console.error(error);
  process.exit(1);
});
