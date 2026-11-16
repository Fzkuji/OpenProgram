import assert from "node:assert/strict";
import test from "node:test";

const { codeSpanPath, linkHrefPath, localPathHref, isAppRoute, relativeTo, fileName } = await import("../../../lib/files/prose-file-paths.ts");

test("a code span that names a file becomes a path to ask about", () => {
  assert.equal(codeSpanPath("exports/plan.pptx"), "exports/plan.pptx");
  assert.equal(codeSpanPath("/Users/me/Q3 Deck/plan.pptx"), "/Users/me/Q3 Deck/plan.pptx");
  assert.equal(codeSpanPath("~/Desktop/report.docx"), "~/Desktop/report.docx");
  assert.equal(codeSpanPath("report.pdf"), "report.pdf");
  assert.equal(codeSpanPath("src/app.py:12"), "src/app.py", "an editor location is not part of the name");
  assert.equal(codeSpanPath("src/app.py:12:4"), "src/app.py");
  assert.equal(codeSpanPath("file:///tmp/out.xlsx"), "/tmp/out.xlsx");
});

test("code that only looks like a word stays code", () => {
  for (const value of ["npm run build", "useState", "x = 1", "a|b", "$HOME/x", "src/", "..", "https://example.com/a.pdf", "foo()", "*.pptx", "line one\nline two", "a.b c"]) {
    assert.equal(codeSpanPath(value), null, value);
  }
});

test("a markdown link to a local path is a file; web addresses and app pages are not", () => {
  assert.equal(linkHrefPath("/Users/me/out/plan.pptx"), "/Users/me/out/plan.pptx");
  assert.equal(linkHrefPath("/Users/me/out/Q3%20plan.pptx:3"), "/Users/me/out/Q3 plan.pptx");
  assert.equal(linkHrefPath("exports/plan.pptx"), "exports/plan.pptx");
  assert.equal(linkHrefPath("file:///tmp/a.pdf"), "/tmp/a.pdf");
  for (const href of ["https://example.com/a.pdf", "mailto:a@b.c", "#note", "//cdn/x.js", "/s/s-37d993#m-6c0a98", "/settings/providers", "/chat"]) {
    assert.equal(linkHrefPath(href), null, href);
  }
});

test("only an href that can name nothing but a local file is held back on click", () => {
  assert.equal(localPathHref("/Users/me/out/plan.pptx"), "/Users/me/out/plan.pptx");
  assert.equal(localPathHref("~/Desktop/report.docx"), "~/Desktop/report.docx");
  assert.equal(localPathHref("./notes/plan.md"), "./notes/plan.md");
  assert.equal(localPathHref("../plan.md"), "../plan.md");
  assert.equal(localPathHref("file:///tmp/a.pdf"), "/tmp/a.pdf");
  assert.equal(localPathHref("C:\\Users\\me\\plan.pptx"), "C:\\Users\\me\\plan.pptx");
  assert.equal(localPathHref("C:/Users/me/plan.pptx"), "C:/Users/me/plan.pptx");
  // A web address without its scheme, or a bare relative name, may be a
  // file only once the worker says so; until then it stays a link.
  for (const href of ["docs.example.ai", "example.com/path", "www.example.com", "notes/plan.md", "plan.pptx",
    "https://example.com/a.pdf", "//cdn/x.js", "/settings", "/s/s-1#m-2", "#note", "mailto:a@b.c"]) {
    assert.equal(localPathHref(href), null, href);
  }
});

test("a bare href is still asked about, a drive is a path, a scheme is not", () => {
  assert.equal(linkHrefPath("docs.example.ai"), "docs.example.ai", "asked about, never assumed");
  assert.equal(linkHrefPath("C:/Users/me/plan.pptx"), "C:/Users/me/plan.pptx");
  assert.equal(codeSpanPath("C:\\out\\plan.pptx"), "C:\\out\\plan.pptx");
  assert.equal(linkHrefPath("ftp:/x/a.pdf"), null);
});

test("app routes are told apart by their first segment", () => {
  assert.equal(isAppRoute("/s/abc"), true);
  assert.equal(isAppRoute("/projects"), true);
  assert.equal(isAppRoute("/Users/me/projects/a.md"), false);
  assert.equal(isAppRoute("projects/a.md"), false);
});

test("relative path and name of an absolute path", () => {
  assert.equal(relativeTo("/work/repo/docs/a.md", "/work/repo/"), "docs/a.md");
  assert.equal(relativeTo("/work/other/a.md", "/work/repo"), null);
  assert.equal(relativeTo("/work/repo/a.md", null), null);
  assert.equal(fileName("/work/repo/docs/a.md"), "a.md");
});
