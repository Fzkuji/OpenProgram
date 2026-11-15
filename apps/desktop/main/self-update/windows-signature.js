"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execFile, execFileSync } = require("node:child_process");
const { promisify } = require("node:util");

// The path travels in the environment: powershell.exe joins whatever follows
// -Command into the command text, so a script there has no $args to read.
const SIGNATURE_PATH_VARIABLE = "OPENPROGRAM_SIGNATURE_PATH";
const SIGNATURE_SCRIPT = [
  "$ErrorActionPreference = 'Stop'",
  `$signature = Get-AuthenticodeSignature -LiteralPath $env:${SIGNATURE_PATH_VARIABLE}`,
  "[pscustomobject]@{ status = [string]$signature.Status; subject = [string]$signature.SignerCertificate.Subject; thumbprint = [string]$signature.SignerCertificate.Thumbprint } | ConvertTo-Json -Compress",
].join("; ");

const POWERSHELL_OPTIONS = {
  encoding: "utf8",
  windowsHide: true,
  timeout: 30_000,
};

const SIGNATURE_ARGUMENTS = [
  "-NoLogo",
  "-NoProfile",
  "-NonInteractive",
  "-ExecutionPolicy",
  "Bypass",
  "-Command",
  SIGNATURE_SCRIPT,
];

function powershellOptions(filePath) {
  return {
    ...POWERSHELL_OPTIONS,
    env: { ...process.env, [SIGNATURE_PATH_VARIABLE]: filePath },
  };
}

function parseSignature(output) {
  try {
    return JSON.parse(String(output).trim());
  } catch (_error) {
    throw new Error("Windows update signature result is invalid");
  }
}

function validSigner(signature) {
  return signature?.status === "Valid"
    && typeof signature.subject === "string"
    && Boolean(signature.subject)
    && /^[A-Fa-f0-9]{40,64}$/.test(signature.thumbprint || "");
}

/**
 * Which update line this installed App is on. The release build decides it by
 * signing the App or not, and the answer lives in the App's own file, so nothing
 * downloaded can change it: a signed App stays on the signed line with its own
 * publisher, and only an App built unsigned takes unsigned updates. An App
 * whose signature cannot be read counts as signed with no publisher known;
 * verifyWindowsAuthenticode reads it again before it accepts an installer.
 */
async function readWindowsUpdateLine(
  executable = process.execPath,
  { platform = process.platform, execFileImpl = promisify(execFile) } = {},
) {
  if (platform !== "win32") return null;
  let signature;
  try {
    const { stdout } = await execFileImpl("powershell.exe", SIGNATURE_ARGUMENTS, powershellOptions(executable));
    signature = parseSignature(stdout);
  } catch (_error) {
    return { signed: true, subject: "", thumbprint: "" };
  }
  if (validSigner(signature)) {
    return { signed: true, subject: signature.subject, thumbprint: signature.thumbprint.toUpperCase() };
  }
  return { signed: signature?.status !== "NotSigned", subject: "", thumbprint: "" };
}

/**
 * Check a downloaded installer against the running App's line. It runs after
 * the download has matched the release manifest's size and SHA-256, which is
 * the only thing vouching for an installer on the unsigned line.
 */
function readSignatureSync(filePath, execFileSyncImpl) {
  return parseSignature(execFileSyncImpl("powershell.exe", SIGNATURE_ARGUMENTS, {
    ...powershellOptions(filePath),
    stdio: ["ignore", "pipe", "pipe"],
  }));
}

/**
 * The publisher a signed App's installer must match. A line read without one
 * (PowerShell failed, timed out, or the App's signature is damaged) proves
 * nothing about who published this App, so the App is read once more here and
 * the installer is refused when that still gives no publisher.
 */
function linePublisher(line, executable, execFileSyncImpl) {
  if (line.thumbprint && line.subject) return line;
  let own = null;
  try {
    own = readSignatureSync(executable, execFileSyncImpl);
  } catch (_error) {
    own = null;
  }
  if (!validSigner(own)) {
    throw new Error("Windows update cannot be checked: this App's own publisher could not be read");
  }
  return { subject: own.subject, thumbprint: own.thumbprint.toUpperCase() };
}

function verifyWindowsAuthenticode(
  filePath,
  {
    platform = process.platform,
    existsSync = fs.existsSync,
    execFileSyncImpl = execFileSync,
    line = null,
    executable = process.execPath,
  } = {},
) {
  if (platform !== "win32") return null;
  if (!path.win32.isAbsolute(filePath) || !existsSync(filePath)) {
    throw new Error("Windows update installer is missing");
  }
  let signature;
  try {
    signature = readSignatureSync(filePath, execFileSyncImpl);
  } catch (error) {
    if (error?.message === "Windows update signature result is invalid") throw error;
    throw new Error("Windows update signature verification failed");
  }
  if (line && !line.signed && signature?.status === "NotSigned") {
    return { subject: "", thumbprint: "", unsigned: true };
  }
  if (!validSigner(signature)) {
    throw new Error(`Windows update signature is not valid: ${signature?.status || "Unknown"}`);
  }
  const thumbprint = signature.thumbprint.toUpperCase();
  if (line?.signed) {
    const publisher = linePublisher(line, executable, execFileSyncImpl);
    // A renewed certificate has a new thumbprint and the same publisher name.
    if (thumbprint !== publisher.thumbprint && signature.subject !== publisher.subject) {
      throw new Error("Windows update is signed by a different publisher than this App");
    }
  }
  return { subject: signature.subject, thumbprint };
}

module.exports = { readWindowsUpdateLine, verifyWindowsAuthenticode };
