// The files electron-builder packs into app.asar, expanded from
// package.json build.files. An entry is either one file ("main.js") or a
// whole folder ("main/**", dotfiles included).
const fs = require("node:fs");
const path = require("node:path");

const desktopRoot = path.join(__dirname, "..");

function filesUnder(folder) {
  const found = [];
  for (const entry of fs.readdirSync(path.join(desktopRoot, folder), { withFileTypes: true })) {
    const name = `${folder}/${entry.name}`;
    if (entry.isDirectory()) found.push(...filesUnder(name));
    else found.push(name);
  }
  return found;
}

function packagedFiles() {
  const entries = require(path.join(desktopRoot, "package.json")).build.files;
  const files = new Set();
  for (const entry of entries) {
    if (entry.endsWith("/**")) for (const name of filesUnder(entry.slice(0, -3))) files.add(name);
    else files.add(entry);
  }
  return files;
}

module.exports = { packagedFiles };
