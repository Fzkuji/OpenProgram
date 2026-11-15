import assert from "node:assert/strict";
import test from "node:test";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { registerHooks } from "node:module";
import { fileURLToPath } from "node:url";
const root = new URL("../../", import.meta.url);
registerHooks({ resolve(specifier, context, next) {
  const base = specifier.startsWith("@/") ? new URL(specifier.slice(2), root) : specifier.startsWith(".") ? new URL(specifier, context.parentURL) : null;
  if (base) for (const suffix of [".ts", "/index.ts"]) if (existsSync(fileURLToPath(base.href + suffix))) return { url: base.href + suffix, shortCircuit: true };
  return next(specifier, context);
} });
const { untitledName } = await import("../../lib/office/office-documents.ts");

test("a new document never takes an existing name: Untitled, then Untitled 2", () => {
  assert.equal(untitledName("docx", 1), "Untitled document.docx");
  assert.equal(untitledName("xlsx", 2), "Untitled spreadsheet 2.xlsx");
  assert.equal(untitledName("pptx", 3), "Untitled presentation 3.pptx");
});

test("the three blank templates ship as real Office packages", async () => {
  for (const kind of ["docx", "xlsx", "pptx"]) {
    const bytes = await readFile(new URL(`../../public/office-templates/blank.${kind}`, import.meta.url));
    assert.equal(bytes.subarray(0, 2).toString(), "PK", `blank.${kind} is a zip package`);
  }
});
