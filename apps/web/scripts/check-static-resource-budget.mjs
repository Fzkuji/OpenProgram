import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const outRoot = join(webRoot, "out");
const entry = join(outRoot, "chat.html");
const TRANSFER_BUDGET = 2 * 1024 * 1024;
const CHUNK_BUDGET = 350 * 1024;
const GZIP_MINIMUM_SIZE = 500;

assert.ok(existsSync(entry), "apps/web/out/chat.html is missing; run the production build first");

function* files(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* files(path);
    else yield path;
  }
}

function localPath(raw) {
  const value = raw.replace(/&amp;/g, "&").split("?", 1)[0].split("#", 1)[0];
  if (!value || /^(?:data:|blob:|#)/i.test(value)) return null;
  assert.doesNotMatch(value, /^https?:\/\//i, `external runtime asset found in build output: ${raw}`);
  if (!value.startsWith("/")) return null;
  const path = resolve(outRoot, `.${value}`);
  assert.ok(path.startsWith(resolve(outRoot) + "/"), `asset escapes out/: ${raw}`);
  return path;
}

function encodedBytes(path) {
  const body = readFileSync(path);
  return body.length >= GZIP_MINIMUM_SIZE ? gzipSync(body, { level: 6 }).length : body.length;
}

const html = readFileSync(entry, "utf8");
assert.doesNotMatch(
  html,
  /<(?:script|link)[^>]+(?:src|href)=["']https?:\/\//i,
  "chat build output must not reference external runtime scripts or styles",
);

const initial = new Set([entry]);
for (const match of html.matchAll(/<(?:script|link|img)\b[^>]+(?:src|href)=["']([^"']+)["'][^>]*>/gi)) {
  const path = localPath(match[1]);
  if (path && existsSync(path) && statSync(path).isFile()) initial.add(path);
}

for (const path of [...initial]) {
  if (extname(path) !== ".css") continue;
  const css = readFileSync(path, "utf8");
  for (const match of css.matchAll(/url\(["']?([^"')]+)["']?\)/g)) {
    const raw = match[1];
    if (/^(?:data:|https?:)/i.test(raw)) continue;
    const asset = raw.startsWith("/")
      ? localPath(raw)
      : resolve(path, "..", raw.split("?", 1)[0]);
    if (asset && existsSync(asset) && statSync(asset).isFile()) initial.add(asset);
  }
}

const transferBytes = [...initial].reduce((total, path) => total + encodedBytes(path), 0);
const chunks = [...files(join(outRoot, "_next", "static", "chunks"))]
  .filter((path) => path.endsWith(".js"));
const largest = chunks
  .map((path) => ({ path, bytes: encodedBytes(path) }))
  .sort((a, b) => b.bytes - a.bytes)[0];

assert.ok(transferBytes <= TRANSFER_BUDGET, `initial encoded transfer ${transferBytes} exceeds ${TRANSFER_BUDGET}`);
assert.ok(largest, "no production JavaScript chunks found");
assert.ok(largest.bytes <= CHUNK_BUDGET, `largest encoded chunk ${largest.bytes} exceeds ${CHUNK_BUDGET}: ${largest.path}`);

console.log(JSON.stringify({
  entry: "chat.html",
  initial_resource_count: initial.size,
  initial_encoded_bytes: transferBytes,
  largest_chunk: largest.path.slice(outRoot.length + 1),
  largest_chunk_encoded_bytes: largest.bytes,
}));
