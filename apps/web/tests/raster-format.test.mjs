import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { inspectRasterBytes, validateRasterInput, validateRasterDecoded, assertEncodedRaster, MAX_RASTER_BYTES } from "../lib/documents/raster-format.ts";
const fixture = (name) => new Uint8Array(readFileSync(new URL(`../../../tests/e2e/web/fixtures/raster/${name}`, import.meta.url)));

test("real static formats expose bounded dimensions before browser allocation", () => {
  for (const [ext,format] of [["png","png"],["jpg","jpeg"],["webp","webp"]]) {
    const value = validateRasterInput(fixture(`quadrants.${ext}`));
    assert.equal(value.format,format); assert.equal(value.width,320); assert.equal(value.height,240);
  }
});
test("actual APNG and animated WebP cannot overwrite their source as a still image", () => {
  for (const ext of ["png","webp"]) {
    assert.equal(inspectRasterBytes(fixture(`animated.${ext}`)).animated,true);
    assert.throws(()=>validateRasterInput(fixture(`animated.${ext}`)),/animated/);
  }
});
test("normal document API octet-stream bytes decode and release the bitmap", async () => {
  const previous=globalThis.createImageBitmap;let closes=0;
  globalThis.createImageBitmap=async()=>({width:320,height:240,close(){closes++;}});
  try {
    const result=await validateRasterDecoded(new Blob([fixture("quadrants.png")],{type:"application/octet-stream"}));
    assert.equal(result.mime,"image/png");assert.equal(closes,1);
    await assert.rejects(validateRasterDecoded(new Blob([fixture("quadrants.png")],{type:"image/jpeg"})),/MIME/);
    assert.equal(closes,1);
  } finally {globalThis.createImageBitmap=previous;}
});
test("byte and header pixel limits reject before reading or decoding image data", async () => {
  class Oversize extends Blob { get size(){return MAX_RASTER_BYTES+1;} async arrayBuffer(){throw Error("unexpected read");} }
  await assert.rejects(validateRasterDecoded(new Oversize()),/IMAGE_RESOURCE_LIMIT/);
  const raw=fixture("quadrants.png");const view=new DataView(raw.buffer,raw.byteOffset,raw.byteLength);
  view.setUint32(16,65536);view.setUint32(20,65536);
  const previous=globalThis.createImageBitmap;let decoded=false;
  globalThis.createImageBitmap=async()=>{decoded=true;throw Error("unexpected decode");};
  try { await assert.rejects(validateRasterDecoded(new Blob([raw])),/IMAGE_RESOURCE_LIMIT/);assert.equal(decoded,false); }
  finally {globalThis.createImageBitmap=previous;}
});
test("encoding checks read Blob bytes and reject a mismatched output or damaged container", async () => {
  await assertEncodedRaster(new Blob([fixture("quadrants.png")],{type:"image/png"}),"png");
  await assert.rejects(assertEncodedRaster(new Blob([fixture("quadrants.jpg")]),"png"),/encoding/);
  const raw=fixture("quadrants.webp");new DataView(raw.buffer,raw.byteOffset).setUint32(16,0xffffffff,true);
  assert.throws(()=>validateRasterInput(raw),/invalid|truncated/i);
  assert.throws(()=>validateRasterInput(fixture("quadrants.png").slice(0,8)),/invalid|truncated/i);
});
