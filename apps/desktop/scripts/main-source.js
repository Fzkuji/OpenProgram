// Source-only checks cover the entry and every composed desktop module.
const fs = require("node:fs");
const path = require("node:path");
function readMainSource() {
  const root = path.join(__dirname, "..");
  return [fs.readFileSync(path.join(root, "main.js"), "utf8"),
    ...fs.readdirSync(path.join(root, "main")).filter((name) => name.endsWith(".js")).sort()
      .map((name) => fs.readFileSync(path.join(root, "main", name), "utf8"))].join("\n");
}
module.exports = { readMainSource };
