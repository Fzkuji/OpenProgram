import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// Dialogs drifted into several looks: the old shadcn default with a black
// 80% overlay, hand-rolled backdrops, and the browser's native confirm().
// Every modal now renders through the Nova Dialog / AlertDialog, and every
// "are you sure?" through the shared ConfirmDialog.
const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFileSync(join(root, path), "utf8");

function sources(dir) {
  const out = [];
  for (const name of readdirSync(join(root, dir))) {
    const path = `${dir}/${name}`;
    if (statSync(join(root, path)).isDirectory()) out.push(...sources(path));
    else if (/\.(tsx?|css)$/.test(name)) out.push(path);
  }
  return out;
}
const all = [...sources("components"), ...sources("lib"), ...sources("app")];

test("confirmations go through the shared ConfirmDialog, never the native confirm()", () => {
  const confirm = read("components/ui/confirm-dialog.tsx");
  // A click outside cancels, like every Dialog; Radix's AlertDialog alone would ignore it.
  assert.match(confirm, /<AlertDialogContent\s+onOverlayClick=\{onCancel\}/);
  assert.match(read("components/ui/alert-dialog.tsx"), /<AlertDialogOverlay onClick=\{onOverlayClick\} \/>/);
  for (const file of all) {
    if (file === "components/ui/confirm-dialog.tsx") continue;
    const source = read(file);
    assert.doesNotMatch(source, /window\.confirm\(|[(!\s]confirm\(/, file);
    assert.doesNotMatch(source, /sessions-list\/confirm-dialog/, file);
  }
});

test("no hand-rolled modal backdrops or dialog surface overrides remain", () => {
  for (const file of all) {
    const source = read(file);
    assert.doesNotMatch(source, /dialogBackdrop|modalBackdrop|branches-merge-modal-backdrop/, file);
    if (/\.tsx$/.test(file)) {
      assert.doesNotMatch(source, /background: "rgba\(0,0,0,0\.5\)"/, file);
      // A red fill under the destructive button's red text.
      assert.doesNotMatch(source, /hover:bg-\[#c9413a\]/, file);
    }
  }
  const useModal = all.filter((file) => /useModalA11y\(/.test(read(file)));
  assert.deepEqual(
    useModal.sort(),
    ["components/chat/messages/attachments/attachment-preview.tsx", "lib/hooks/use-modal-a11y.ts"].sort(),
    "only the attachment lightbox keeps the hand-rolled modal hook",
  );
});
