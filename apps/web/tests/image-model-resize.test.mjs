import assert from "node:assert/strict";
import test from "node:test";

import {
  MAX_MODEL_IMAGE_BYTES,
  MAX_ORIGINAL_IMAGE_BYTES,
  MODEL_IMAGE_LONG_EDGE_CAP,
  MODEL_IMAGE_RESIZE_START,
  imagePreviewDataUrl,
  modelImageOutputMime,
  nextModelResizeLongEdge,
  originalImageBytes,
  pickEncodedModelImage,
  planModelImageDelivery,
  readImageFile,
} from "../components/chat/composer/attach/image-attach.ts";

test("small decoded images keep original bytes and skip resize", () => {
  const plan = planModelImageDelivery({
    byteLength: 1_200_000,
    width: 1600,
    height: 900,
    mime: "image/png",
  });
  assert.deepEqual(plan, { action: "keep-original" });
  assert.equal(imagePreviewDataUrl({
    data: "send-copy",
    media_type: "image/jpeg",
  }), "data:image/jpeg;base64,send-copy");
});

test("oversize bytes or long edge require a 2048-first send copy", () => {
  const byBytes = planModelImageDelivery({
    byteLength: MAX_MODEL_IMAGE_BYTES + 1,
    width: 1200,
    height: 800,
    mime: "image/jpeg",
  });
  assert.equal(byBytes.action, "resize");
  assert.equal(byBytes.outputMime, "image/jpeg");
  assert.equal(byBytes.longEdges[0], 1200);
  assert.ok(byBytes.longEdges[0] <= MODEL_IMAGE_RESIZE_START);

  const byEdge = planModelImageDelivery({
    byteLength: 800_000,
    width: MODEL_IMAGE_LONG_EDGE_CAP + 1,
    height: 100,
    mime: "image/png",
  });
  assert.equal(byEdge.action, "resize");
  assert.equal(byEdge.outputMime, "image/png");
  assert.equal(byEdge.longEdges[0], MODEL_IMAGE_RESIZE_START);
  assert.ok(byEdge.longEdges[1] < MODEL_IMAGE_RESIZE_START);
  assert.equal(modelImageOutputMime("image/gif"), "image/png");
});

test("encode picker walks shrinking edges and fails when none fit", () => {
  const steps = [];
  for (let edge = MODEL_IMAGE_RESIZE_START; edge != null; edge = nextModelResizeLongEdge(edge)) {
    steps.push(edge);
  }
  assert.equal(steps[0], 2048);
  assert.ok(steps.includes(1536));
  assert.deepEqual(
    pickEncodedModelImage([
      { longEdge: 2048, bytes: MAX_MODEL_IMAGE_BYTES + 50_000 },
      { longEdge: 1536, bytes: null },
      { longEdge: 1024, bytes: 4_000_000 },
    ]),
    { longEdge: 1024, bytes: 4_000_000 },
  );
  assert.deepEqual(
    pickEncodedModelImage([
      { longEdge: 2048, bytes: MAX_MODEL_IMAGE_BYTES + 1 },
      { longEdge: 512, bytes: 0 },
      { longEdge: 256, bytes: null },
    ]),
    { fail: true },
  );
});

test("preview and IDB restore prefer original_data over the send copy", () => {
  const attachment = {
    data: "resized",
    media_type: "image/jpeg",
    original_data: "full-png",
    original_media_type: "image/png",
  };
  assert.deepEqual(originalImageBytes(attachment), {
    data: "full-png",
    media_type: "image/png",
  });
  assert.equal(
    imagePreviewDataUrl(attachment),
    "data:image/png;base64,full-png",
  );
});

test("original over the 32MiB attach cap is rejected before a send copy is planned", async () => {
  const tooBig = planModelImageDelivery({
    byteLength: MAX_ORIGINAL_IMAGE_BYTES + 1,
    width: 100,
    height: 100,
    mime: "image/png",
  });
  assert.equal(tooBig.action, "reject");
  assert.match(tooBig.reason, /32/);

  await assert.rejects(
    readImageFile({ type: "image/png", size: MAX_ORIGINAL_IMAGE_BYTES + 10 }),
    /32/,
  );
  await assert.rejects(
    readImageFile({ type: "application/pdf", size: 12 }),
    /unsupported image type/,
  );
});

test("readImageFile encodes a send copy, retains original bytes and closes bitmaps", async () => {
  const previous = new Map(["FileReader", "createImageBitmap", "document"].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  let created = 0, closed = 0;
  try {
    globalThis.FileReader = class {
      readAsDataURL(blob) {
        blob.arrayBuffer().then((bytes) => {
          this.result = `data:${blob.type};base64,${Buffer.from(bytes).toString("base64")}`;
          this.onload();
        });
      }
    };
    globalThis.createImageBitmap = async (_file, options) => {
      created++;
      return { width: options?.resizeWidth ?? 9000, height: options?.resizeHeight ?? 1000, close() { closed++; } };
    };
    globalThis.document = {
      createElement: () => ({
        getContext: (type) => type === "2d" ? { drawImage() {} } : null,
        toBlob(callback, type) { callback(new Blob(["encoded send copy"], { type })); },
      }),
    };
    const value = await readImageFile(new Blob(["original bytes"], {type: "image/png"}), "large.png");
    assert.equal(value.attachment.original_data, Buffer.from("original bytes").toString("base64"));
    assert.equal(value.attachment.data, Buffer.from("encoded send copy").toString("base64"));
    assert.equal(value.attachment.media_type, "image/png");
    assert.equal(created, closed, "all decoded bitmaps must be released");
    URL.revokeObjectURL(value.previewUrl);
    globalThis.createImageBitmap = async () => { throw new Error("invalid image"); };
    await assert.rejects(readImageFile(new Blob(["broken"], {type:"image/png"}), "broken.png"), /decode failed/);
  } finally {
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
