import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  buildAttachmentEnvelope,
  extractAttachmentMentions,
  localAttachmentMention,
  localSourcePath,
} from "../lib/attachment-marker.ts";

const root = new URL("../", import.meta.url);
const attachmentHook = readFileSync(
  new URL("components/chat/composer/attach/use-composer-attachments.ts", root),
  "utf8",
);
const submit = readFileSync(
  new URL("components/chat/composer/use-chat-submit.ts", root),
  "utf8",
);
const bridge = readFileSync(new URL("lib/desktop-bridge.ts", root), "utf8");
const legacySend = readFileSync(
  new URL("components/chat/composer/legacy-send.ts", root),
  "utf8",
);
const preload = readFileSync(new URL("../desktop/preload.js", root), "utf8");

const file = { name: "report final.pdf" };
const path = "/Users/test/Research Files/report final.pdf";
assert.equal(localSourcePath(file, { getPathForFile: (value) => {
  assert.equal(value, file);
  return path;
} }), path);
assert.equal(localSourcePath(file, null), undefined);
assert.equal(localSourcePath(file, { getPathForFile: () => "" }), undefined);
assert.equal(localSourcePath(file, { getPathForFile: () => { throw new Error("denied"); } }), undefined);
assert.equal(
  localSourcePath(file, { getPathForFile: () => "/Users/test/report " }),
  "/Users/test/report ",
  "Electron's non-empty path must be preserved byte-for-byte",
);

assert.equal(
  localAttachmentMention("report final.pdf", "pdf", 2048, path),
  `[attachment: report final.pdf (pdf, 2 KB) @json ${JSON.stringify(path)}]`,
);
assert.equal(
  localAttachmentMention("Project Folder", "", 0, "/Users/test/Project Folder"),
  '[attachment: Project Folder (file, 1 KB) @json "/Users/test/Project Folder"]',
);

const specialPath = '/Users/test/dir]name/report (final).pdf ';
const specialMarker = localAttachmentMention(
  "report (final).pdf", "pdf", 1024, specialPath,
);
const specialParsed = extractAttachmentMentions(`${specialMarker}\n\nexplain`);
assert.deepEqual(specialParsed.mentions, [{
  filename: "report _final_.pdf",
  ext: "pdf",
  kb: "1",
  count: "",
  path: specialPath,
  previewPath: "",
}]);
assert.equal(specialParsed.text.trim(), "explain");
assert.deepEqual(
  extractAttachmentMentions(
    `[attachment: old.txt (txt, 1 KB) @ /tmp/old.txt]\n${specialMarker}`,
  ).mentions.map((mention) => mention.path),
  ["/tmp/old.txt", specialPath],
  "current and historical markers must retain message order",
);
const immutablePreview = "/state/sessions/s1/workdir/attachments/report.pdf";
const previewParsed = extractAttachmentMentions(
  `[attachment: report.pdf (pdf, 1 KB) @json ${JSON.stringify(path)} @previewjson ${JSON.stringify(immutablePreview)}]`,
);
assert.deepEqual(previewParsed.mentions[0], {
  filename: "report.pdf",
  ext: "pdf",
  kb: "1",
  count: "",
  path,
  previewPath: immutablePreview,
});

const envelope = buildAttachmentEnvelope(
  [{
    sourcePath: "/Users/test/photo.png",
    sizeBytes: 3072,
    attachment: {
      type: "image", data: "image-b64", media_type: "image/png", filename: "photo.png",
    },
  }, {
    sizeBytes: 1024,
    attachment: {
      type: "image", data: "browser-image", media_type: "image/png", filename: "pasted.png",
    },
  }],
  [{
    filename: "notes.txt", ext: "txt", sizeBytes: 2048,
    sourcePath: "/Users/test/notes.txt", dataB64: "doc-b64", mediaType: "text/plain",
  }, {
    filename: "Project Folder", ext: "", sizeBytes: 0,
    sourcePath: "/Users/test/Project Folder", dataB64: null,
  }, {
    filename: "browser.pdf", ext: "pdf", sizeBytes: 4096,
    dataB64: "browser-doc", mediaType: "application/pdf",
  }, {
    filename: "browser-large.bin", ext: "bin", sizeBytes: 4096,
    dataB64: null,
  }],
);
assert.deepEqual(envelope.mentions, [
  '[attachment: photo.png (png, 3 KB) @json "/Users/test/photo.png"]',
  '[attachment: notes.txt (txt, 2 KB) @json "/Users/test/notes.txt"]',
  '[attachment: Project Folder (file, 1 KB) @json "/Users/test/Project Folder"]',
  "[attachment: browser.pdf (pdf, 4 KB)]",
  "[attachment: browser-large.bin (bin, 4 KB, too large — not sent)]",
]);
assert.deepEqual(envelope.imagesPayload, [{
  type: "image", data: "image-b64", media_type: "image/png", filename: "photo.png",
  source_path: "/Users/test/photo.png",
}, {
  type: "image", data: "browser-image", media_type: "image/png", filename: "pasted.png",
}]);
assert.deepEqual(envelope.docsPayload, [{
  type: "document", data: "doc-b64", media_type: "text/plain", filename: "notes.txt",
  source_path: "/Users/test/notes.txt",
}, {
  type: "document", data: "browser-doc", media_type: "application/pdf", filename: "browser.pdf",
}]);

assert.match(preload, /webUtils\.getPathForFile\(file\)/, "preload must use Electron webUtils");
assert.match(bridge, /getPathForFile\?\(file:\s*File\):\s*string/, "desktop bridge must type the path accessor");
assert.match(
  attachmentHook.slice(
    attachmentHook.indexOf("const imagePlaceholders"),
    attachmentHook.indexOf("const docPlaceholders"),
  ),
  /sourcePath:\s*localSourcePath\(f,\s*desktopBridge\(\)\)/,
  "image placeholders must capture the original path",
);
assert.match(
  attachmentHook.slice(
    attachmentHook.indexOf("const docPlaceholders"),
    attachmentHook.indexOf(
      "addImagesForOwner(ownerKey",
      attachmentHook.indexOf("const docPlaceholders"),
    ),
  ),
  /sourcePath:\s*localSourcePath\(f,\s*desktopBridge\(\)\)/,
  "document and folder placeholders must capture the original path",
);
assert.match(submit, /buildAttachmentEnvelope\(\s*pendingImages,\s*pendingDocs,?\s*\)/,
  "submit must use the behavior-tested envelope builder");
const messageParser = readFileSync(
  new URL("components/chat/messages/user-attachments.tsx", root),
  "utf8",
);
assert.match(messageParser, /extractAttachmentMentions\(text\)/,
  "the displayed chip parser must use the behavior-tested marker parser");
assert.match(messageParser, /path:\s*mention\.previewPath\s*\|\|\s*mention\.path/,
  "the UI must preview the immutable session copy instead of a mutable source path");
assert.match(legacySend, /a\.source_path[\s\S]*source_path:\s*a\.source_path/,
  "the websocket payload must preserve per-attachment path provenance");

console.log("local attachment path check passed");
