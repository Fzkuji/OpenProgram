"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const SIGNATURE_SCRIPT = [
  "$ErrorActionPreference = 'Stop'",
  "$signature = Get-AuthenticodeSignature -LiteralPath $args[0]",
  "[pscustomobject]@{ status = [string]$signature.Status; subject = [string]$signature.SignerCertificate.Subject; thumbprint = [string]$signature.SignerCertificate.Thumbprint } | ConvertTo-Json -Compress",
].join("; ");

function verifyWindowsAuthenticode(
  filePath,
  {
    platform = process.platform,
    existsSync = fs.existsSync,
    execFileSyncImpl = execFileSync,
  } = {},
) {
  if (platform !== "win32") return null;
  if (!path.isAbsolute(filePath) || !existsSync(filePath)) {
    throw new Error("Windows update installer is missing");
  }
  let output;
  try {
    output = execFileSyncImpl(
      "powershell.exe",
      [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        SIGNATURE_SCRIPT,
        filePath,
      ],
      {
        encoding: "utf8",
        windowsHide: true,
        timeout: 30_000,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
  } catch (_error) {
    throw new Error("Windows update signature verification failed");
  }
  let signature;
  try {
    signature = JSON.parse(String(output).trim());
  } catch (_error) {
    throw new Error("Windows update signature result is invalid");
  }
  if (
    signature?.status !== "Valid"
    || typeof signature.subject !== "string"
    || !signature.subject
    || !/^[A-Fa-f0-9]{40,64}$/.test(signature.thumbprint || "")
  ) {
    throw new Error(`Windows update signature is not valid: ${signature?.status || "Unknown"}`);
  }
  return {
    subject: signature.subject,
    thumbprint: signature.thumbprint.toUpperCase(),
  };
}

module.exports = { verifyWindowsAuthenticode };
