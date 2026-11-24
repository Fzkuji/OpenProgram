"use strict";

// Installing a downloaded update in place: the App prepares the new copy while
// it keeps running, and the user's "Restart to update" swaps it in. Each
// platform gets one object with the same four members:
//
//   enabled        whether this App can replace itself at all
//   downloadPath   where the release file goes; no save dialog
//   prepare        check the file and get it ready to swap in
//   install        quit, swap, start the new copy; "" or why it could not
//
// A platform that cannot replace itself (an App in a folder it cannot write,
// a local build, a .deb under /opt) returns null and keeps the download that
// opens the installer.

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

/** The .app bundle of an executable at <bundle>/Contents/MacOS/<name>. */
function macAppBundle(execPath) {
  const bundle = path.resolve(execPath, "..", "..", "..");
  return bundle.endsWith(".app") ? bundle : null;
}

/** The Developer ID team that signed a bundle, or null for an ad-hoc or local signature. */
function macTeamIdentifier(bundle, run = spawnSync) {
  const result = run("/usr/bin/codesign", ["-dv", "--verbose=2", bundle], { encoding: "utf8", timeout: 15_000 });
  const match = /^TeamIdentifier=(.+)$/m.exec(`${result.stderr || ""}\n${result.stdout || ""}`);
  const team = match ? match[1].trim() : "";
  return team && team !== "not set" ? team : null;
}

function writable(target) {
  try {
    fs.accessSync(target, fs.constants.W_OK);
    return true;
  } catch {
    return false;
  }
}

function runChecked(run, command, args, what) {
  const result = run(command, args, { encoding: "utf8", timeout: 300_000 });
  if (result.error || result.status !== 0) {
    const detail = String(result.stderr || result.error?.message || "").trim().split("\n").at(-1);
    throw new Error(`${what}${detail ? `: ${detail}` : ""}`);
  }
  return result;
}

// Waits for the App to exit, swaps the bundles with two renames, puts the old
// one back if the second fails, and opens whichever is in place.
const MAC_SWAP_SCRIPT = `
pid="$1"; app="$2"; new="$3"; opener="\${4:-open}"; old="$app.replaced-$$"
i=0
while kill -0 "$pid" 2>/dev/null; do
  sleep 0.2; i=$((i+1))
  if [ "$i" -gt 600 ]; then exit 1; fi
done
if mv "$app" "$old"; then
  if mv "$new" "$app"; then rm -rf "$old"; else mv "$old" "$app"; fi
fi
"$opener" "$app"
`;

/**
 * Replace a signed macOS App from the release's .zip. Only an App signed by a
 * Developer ID team takes part, and only a copy signed by the same team is
 * swapped in, so a local build never becomes a downloaded one.
 */
function macInPlace({ execPath, userData, quit, run = spawnSync, start = spawn, pid = process.pid }) {
  const bundle = macAppBundle(execPath);
  if (!bundle) return null;
  if (bundle.includes("/AppTranslocation/") || bundle.startsWith("/Volumes/")) return null;
  if (!writable(path.dirname(bundle)) || !writable(bundle)) return null;
  const team = macTeamIdentifier(bundle, run);
  if (!team) return null;
  const updates = path.join(userData, "updates");
  const staged = path.join(updates, "staged");
  // The release zip holds OpenProgram.app (ditto --keepParent); it takes the
  // running bundle's place whatever that one is called.
  const stagedApp = path.join(staged, "OpenProgram.app");
  return {
    kind: "app-archive",
    downloadPath: (name) => path.join(updates, name),
    async prepare(file, release) {
      fs.rmSync(staged, { recursive: true, force: true });
      fs.mkdirSync(staged, { recursive: true });
      runChecked(run, "/usr/bin/ditto", ["-x", "-k", file, staged], "the update could not be unpacked");
      if (!fs.existsSync(stagedApp)) throw new Error("the update has no OpenProgram.app");
      runChecked(run, "/usr/bin/codesign", ["--verify", "--deep", "--strict", stagedApp], "the update's signature is invalid");
      if (macTeamIdentifier(stagedApp, run) !== team) throw new Error("the update is signed by a different developer");
      const plist = path.join(stagedApp, "Contents", "Info.plist");
      const version = runChecked(
        run, "/usr/bin/plutil", ["-extract", "CFBundleShortVersionString", "raw", plist], "the update's version is unreadable",
      ).stdout.trim();
      if (version !== release.latestVersion) throw new Error(`the update is ${version}, not ${release.latestVersion}`);
    },
    async install() {
      if (!fs.existsSync(stagedApp)) return "The prepared update is gone. Check for updates again.";
      const child = start("/bin/sh", ["-c", MAC_SWAP_SCRIPT, "swap", String(pid), bundle, stagedApp], {
        detached: true, stdio: "ignore",
      });
      child.unref();
      quit();
      return "";
    },
  };
}

/**
 * Run the release's NSIS installer silently once the App has quit; it starts
 * the new App when it is done.
 */
function windowsInPlace({ userData, quit, start = spawn }) {
  const updates = path.join(userData, "updates");
  return {
    kind: "windows-installer",
    downloadPath: (name) => path.join(updates, name),
    async prepare() {},
    async install(file) {
      if (!fs.existsSync(file)) return "The downloaded update is gone. Check for updates again.";
      const child = start(file, ["/S", "--updated", "--force-run"], {
        detached: true, stdio: "ignore", windowsHide: true,
      });
      child.unref();
      quit();
      return "";
    },
  };
}

/** Put a checked AppImage where the running one lives. */
function replaceAppImage(downloaded, appImagePath) {
  fs.chmodSync(downloaded, 0o755);
  fs.renameSync(downloaded, appImagePath);
}

/** An AppImage downloads beside itself and takes the new file's place on restart. */
function appImageInPlace({ appImage, relaunch }) {
  if (!appImage || !writable(path.dirname(appImage))) return null;
  return {
    kind: "appimage",
    downloadPath: (name) => path.join(path.dirname(appImage), `.${name}.download`),
    async prepare(file) { fs.chmodSync(file, 0o755); },
    async install(file) {
      try {
        replaceAppImage(file, appImage);
      } catch (error) {
        return `The new version could not replace ${appImage}: ${error.message}`;
      }
      relaunch();
      return "";
    },
  };
}

module.exports = {
  MAC_SWAP_SCRIPT,
  appImageInPlace,
  macAppBundle,
  macInPlace,
  macTeamIdentifier,
  replaceAppImage,
  windowsInPlace,
};
