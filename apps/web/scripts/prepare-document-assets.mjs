/** Pinned local decoder assets. No runtime CDN or document upload is required. */
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cpSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
const require = createRequire(import.meta.url);
const source = dirname(require.resolve("pdfjs-dist/package.json"));
const pkg = JSON.parse(readFileSync(join(source, "package.json"), "utf8"));
if (pkg.version !== "6.3.289") throw new Error("PDF asset version does not match the reviewed decoder.");
const target = fileURLToPath(new URL("../public/document-assets/pdfjs/", import.meta.url));
rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
for (const name of ["pdf.mjs", "pdf.worker.mjs"]) cpSync(join(source, "build", name), join(target, name));
for (const name of ["cmaps", "standard_fonts", "wasm", "LICENSE"]) cpSync(join(source, name), join(target, name), { recursive: true });
function inventory(dir, prefix = "") {
  return readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name)).flatMap((entry) => {
    const name = prefix + entry.name;
    return entry.isDirectory() ? inventory(join(dir, entry.name), `${name}/`) : [{ path: name,
      sha256: createHash("sha256").update(readFileSync(join(dir, entry.name))).digest("hex") }];
  });
}
writeFileSync(join(target, "manifest.json"), JSON.stringify({ package: pkg.name, version: pkg.version, license: pkg.license, files: inventory(target) }, null, 2) + "\n");
console.log(`Prepared local PDF decoder ${pkg.version}`);

// Retain licenses for the two bundled layered-image decoders and their inflater.
const licenses = fileURLToPath(new URL("../public/document-assets/licenses/", import.meta.url));
mkdirSync(licenses, { recursive: true });
for (const name of ["ag-psd", "utif", "pako"]) {
  const packageRoot = dirname(require.resolve(`${name}/package.json`));
  cpSync(join(packageRoot, "LICENSE"), join(licenses, `${name}.txt`));
}
