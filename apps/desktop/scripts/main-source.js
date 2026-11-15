// Source-only checks cover the entry and every composed main-process module
// in the main/ feature folders (main/preload.js runs in the renderer and is
// read on its own).
const fs = require("node:fs");
const path = require("node:path");
function readMainSource() {
  const root = path.join(__dirname, "..");
  const main = path.join(root, "main");
  const modules = fs.readdirSync(main, { withFileTypes: true }).filter((entry) => entry.isDirectory())
    .flatMap((folder) => fs.readdirSync(path.join(main, folder.name)).filter((name) => name.endsWith(".js"))
      .map((name) => path.join(main, folder.name, name)))
    .sort();
  return [fs.readFileSync(path.join(root, "main.js"), "utf8"),
    ...modules.map((file) => fs.readFileSync(file, "utf8"))].join("\n");
}
module.exports = { readMainSource };
