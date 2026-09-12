import assert from "node:assert/strict";
import test from "node:test";

const { inspectRasterBytes, rasterMime, validateRasterInput } = await import("../lib/documents/raster-format.ts");

test("recognizes static raster signatures and rejects animation", () => {
  assert.equal(rasterMime(new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])), "image/png");
  assert.equal(rasterMime(new Uint8Array([0xff, 0xd8, 0xff])), "image/jpeg");
  const webp = new Uint8Array(24); webp.set([...Buffer.from("RIFF"), 0, 0, 0, 0, ...Buffer.from("WEBP"), ...Buffer.from("VP8X"), 0, 0, 0, 0, 0, 0, 0, 0]);
  assert.equal(rasterMime(webp), "image/webp");
  const apng = new Uint8Array(40); apng.set([0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a], 0); apng.set([...Buffer.from("acTL")], 20);
  assert.equal(inspectRasterBytes(apng).animated, true);
  assert.throws(() => validateRasterInput(apng), /animated/);
});

test("accepts only same-format static PNG/JPEG/WebP", () => {
  assert.equal(validateRasterInput(new Uint8Array([0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a])).format, "png");
  assert.throws(() => validateRasterInput(new Uint8Array([1, 2, 3])), /unsupported|invalid/i);
  assert.throws(() => validateRasterInput(new Uint8Array([0x47, 0x49, 0x46])), /UNSUPPORTED/);
});
