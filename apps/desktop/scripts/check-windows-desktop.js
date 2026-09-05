"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {
  killWindowsProcessTree,
  resolveTerminalCommand,
  waitForTerminalPid,
} = require("../terminal-command");
const { verifyWindowsAuthenticode } = require("../windows-signature");
const {
  browserWindowChromeOptions,
  applyNativeTitleBarChrome,
} = require("../window-chrome");

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
  trafficLightPosition: { x: 18, y: 13 },
});
assert.deepEqual(browserWindowChromeOptions("linux", chrome), {});

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
const mainSource = fs.readFileSync(path.join(desktopRoot, "main.js"), "utf8");
const preloadSource = fs.readFileSync(path.join(desktopRoot, "preload.js"), "utf8");
const chromeCss = fs.readFileSync(
  path.resolve(desktopRoot, "..", "web", "app", "styles", "base.css"),
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
  path.join(desktopRoot, "packaged-runtime.js"),
  "utf8",
);
const workerStartUrlSource = fs.readFileSync(
  path.join(desktopRoot, "worker-start-url.js"),
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
    assert.equal(args.at(-1), "C:\\Downloads\\OpenProgram.exe");
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

async function checkLiveConpty() {
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

checkLiveConpty().then(() => {
  console.log("Windows Desktop checks passed");
  process.exit(0);
}).catch((error) => {
  console.error(error);
  process.exit(1);
});
