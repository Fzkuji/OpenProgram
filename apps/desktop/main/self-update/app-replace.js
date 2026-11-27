"use strict";

// Replacing the App itself on "Restart to update".
//
// The updater (openprogram upgrade --prepare) assembled a newer App from its
// shell layer in a hidden folder beside the App, on the App's own drive
// (<App>/../.<App name>.update/staged/), checked it and recorded it in
// <runtime>/shell/staged.json. On restart a detached script waits for the App
// to exit, swaps the new App in with renames (undone if one fails), and opens
// it. Only after a swap does it leave <runtime>/shell/activate, and the new App
// switches to the prepared version as it starts (update-service.js).
//
//   macOS    the .app bundle, by /bin/sh
//   Windows  the install folder, by PowerShell, which also sets the uninstall
//            entry's DisplayVersion to the new App's version
//   Linux    the AppImage file, by /bin/sh; one rename over the running file
//
// A deb under /opt replaces nothing itself, since only root writes there, and
// keeps the download that opens the installer.

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

/** The .app bundle of an executable at <bundle>/Contents/MacOS/<name>. */
function macAppBundle(execPath) {
  const bundle = path.resolve(execPath, "..", "..", "..");
  return bundle.endsWith(".app") ? bundle : null;
}

function writable(target) {
  try {
    fs.accessSync(target, fs.constants.W_OK);
    return true;
  } catch {
    return false;
  }
}

// $1 pid, $2 running App, $3 staged App, $4 opener ("" runs the App itself),
// $5 marker file, $6 its text. The staged App is beside the running one, so
// each mv is a rename on one drive.
const POSIX_SWAP_SCRIPT = `
pid="$1"; app="$2"; new="$3"; opener="$4"; marker="$5"; key="$6"; old="$app.replaced-$$"
i=0
while kill -0 "$pid" 2>/dev/null; do
  sleep 0.2; i=$((i+1))
  if [ "$i" -gt 600 ]; then exit 1; fi
done
if mv "$app" "$old"; then
  if mv "$new" "$app"; then
    rm -rf "$old"
    rmdir "$(dirname "$new")" "$(dirname "$(dirname "$new")")" 2>/dev/null
    if [ -n "$marker" ]; then printf '%s' "$key" > "$marker"; fi
  else
    mv "$old" "$app"
  fi
fi
if [ -n "$opener" ]; then "$opener" "$app"; else "$app" >/dev/null 2>&1 & fi
`;

// The same on Windows, where a folder can be renamed only once nothing runs
// from it: the renames are tried for a while after the App exits. The
// uninstall entry NSIS wrote (HKCU, found by its InstallLocation) then names
// the new version.
const WINDOWS_SWAP_SCRIPT = `
param([int]$AppPid, [string]$App, [string]$New, [string]$Executable, [string]$Marker, [string]$Key, [string]$Version)
$ErrorActionPreference = "Stop"
for ($i = 0; $i -lt 600 -and (Get-Process -Id $AppPid -ErrorAction SilentlyContinue); $i++) {
  Start-Sleep -Milliseconds 200
}
$Old = "$App.replaced-$PID"
$Moved = $false
for ($i = 0; $i -lt 150 -and -not $Moved; $i++) {
  try { Move-Item -LiteralPath $App -Destination $Old; $Moved = $true } catch { Start-Sleep -Milliseconds 200 }
}
$Swapped = $false
if ($Moved) {
  # Only the two renames are undone together; nothing after them can undo the swap.
  try {
    Move-Item -LiteralPath $New -Destination $App
    $Swapped = $true
  } catch {
    Move-Item -LiteralPath $Old -Destination $App
  }
}
if ($Swapped) {
  try {
    $Folder = $App.TrimEnd("\\")
    Get-ChildItem -Path "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall" -ErrorAction SilentlyContinue |
      Where-Object { ([string]$_.GetValue("InstallLocation")).TrimEnd("\\") -ieq $Folder } |
      ForEach-Object { Set-ItemProperty -LiteralPath $_.PSPath -Name DisplayVersion -Value $Version }
  } catch { }
  try { if ($Marker) { [System.IO.File]::WriteAllText($Marker, $Key) } } catch { }
  Remove-Item -LiteralPath (Split-Path -Parent (Split-Path -Parent $New)) -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $Old -Recurse -Force -ErrorAction SilentlyContinue
}
Start-Process -FilePath (Join-Path $App $Executable)
`;

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

/** The running App's root: the .app bundle, the Windows install folder, or the AppImage file. */
function appRoot(execPath, platform, appImage) {
  if (platform === "darwin") return macAppBundle(execPath);
  if (platform === "win32") return path.dirname(execPath);
  if (platform === "linux") return appImage || null;
  return null;
}

/** Where the updater stages a new App: a hidden folder beside the App (official/shell.py's staging_root). */
function stagingRoot(root) {
  return path.join(path.dirname(root), `.${path.basename(root)}.update`);
}

/**
 * Swapping in an App shell the updater staged. `isNewer(version)` says whether
 * a version is newer than the running App's; `shellSwap` returns null where
 * the App cannot replace itself (a deb, a translocated or read-only App).
 */
function shellSwap({
  runtimeRoot, execPath, platform = process.platform, appImage = "", appVersion = "", isNewer, quit,
  start = spawn, pid = process.pid, scriptDirectory,
}) {
  const root = appRoot(execPath, platform, appImage);
  if (!runtimeRoot || !root) return null;
  if (root.includes("/AppTranslocation/") || root.startsWith("/Volumes/")) return null;
  if (!writable(path.dirname(root))) return null;
  const shellDirectory = path.join(runtimeRoot, "shell");
  const stagedDirectory = path.join(stagingRoot(root), "staged");
  const marker = path.join(shellDirectory, "activate");
  return {
    /** This App, as the updater's answer about swapping is kept for it. */
    id: `${root}@${appVersion}`,
    /** Whether a shell of `version` would replace this App. */
    isNewer(version) {
      try {
        return Boolean(version) && isNewer(version);
      } catch {
        return false;
      }
    },
    /** The staged App newer than this one, or null: { path, version, executable }. */
    staged() {
      const record = readJson(path.join(shellDirectory, "staged.json"));
      if (!record || typeof record.path !== "string" || typeof record.version !== "string") return null;
      if (record.app !== root || !fs.existsSync(record.path)) return null;
      if (path.basename(record.path) !== path.basename(root)) return null;
      try {
        if (path.dirname(fs.realpathSync(record.path)) !== fs.realpathSync(stagedDirectory)) return null;
      } catch {
        return null;
      }
      if (typeof record.executable !== "string" || !fs.existsSync(path.join(record.path, record.executable))) return null;
      if (!this.isNewer(record.version)) return null;
      return { path: record.path, version: record.version, executable: record.executable };
    },
    /** Quit and swap; the new App switches to the prepared version named by `key` as it starts. */
    install(staged, key = "") {
      fs.rmSync(marker, { force: true });
      let child;
      if (platform === "win32") {
        const script = path.join(scriptDirectory || shellDirectory, "swap-app.ps1");
        fs.mkdirSync(path.dirname(script), { recursive: true });
        fs.writeFileSync(script, WINDOWS_SWAP_SCRIPT, "utf8");
        const system = process.env.SystemRoot || "C:\\Windows";
        child = start(path.join(system, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"), [
          "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script,
          "-AppPid", String(pid), "-App", root, "-New", staged.path, "-Executable", path.basename(execPath),
          "-Marker", key ? marker : "", "-Key", key, "-Version", staged.version,
        ], { detached: true, stdio: "ignore", windowsHide: true });
      } else {
        child = start("/bin/sh", ["-c", POSIX_SWAP_SCRIPT, "swap", String(pid), root, staged.path,
          platform === "darwin" ? "open" : "", key ? marker : "", key], { detached: true, stdio: "ignore" });
      }
      child.unref();
      quit();
      return "";
    },
    /** What the swap left for this start: the prepared version's key, once. */
    takeActivation() {
      try {
        const key = fs.readFileSync(marker, "utf8").trim();
        fs.rmSync(marker, { force: true });
        return key || null;
      } catch {
        return null;
      }
    },
  };
}

module.exports = {
  POSIX_SWAP_SCRIPT,
  WINDOWS_SWAP_SCRIPT,
  macAppBundle,
  shellSwap,
  stagingRoot,
};
