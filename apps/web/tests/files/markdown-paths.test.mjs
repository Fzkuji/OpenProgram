import assert from "node:assert/strict";
import test from "node:test";

const { resolveMarkdownReference } = await import("../../lib/files/markdown-paths.ts");

test("markdown references resolve against the document's folder", () => {
  assert.equal(resolveMarkdownReference("A4_Promo/post.md", "figures/a.png"), "A4_Promo/figures/a.png");
  assert.equal(resolveMarkdownReference("A4_Promo/post.md", "./figures/a%20b.png?raw=1#x"), "A4_Promo/figures/a b.png");
  assert.equal(resolveMarkdownReference("A4_Promo/post.md", "../shared/b.png"), "shared/b.png");
  assert.equal(resolveMarkdownReference("/Users/me/work/post.md", "figures/a.png"), "/Users/me/work/figures/a.png");
  assert.equal(resolveMarkdownReference("/Users/me/work/post.md", "/tmp/a.png"), "/tmp/a.png");
  assert.equal(resolveMarkdownReference("docs/post.md", "/assets/a.png"), "assets/a.png");
});

test("markdown references that are not files stay untouched", () => {
  for (const reference of ["https://example.com/a.png", "data:image/png;base64,AAAA", "mailto:a@b.c", "//cdn/a.png", "#section", ""]) {
    assert.equal(resolveMarkdownReference("docs/post.md", reference), null, reference);
  }
  assert.equal(resolveMarkdownReference("post.md", "../../etc/passwd"), null);
});

test('local fragments remain distinct from filenames and authorization queries', async () => {
  const {resolveMarkdownFile,markdownDestination,markdownTokenDestination}=await import('../../lib/files/markdown-paths.ts');
  assert.deepEqual(resolveMarkdownFile('docs/post.md','fig%23one.svg?session=other#part%20one'),{path:'docs/fig#one.svg',fragment:'#part%20one'});
  assert.equal(resolveMarkdownFile('post.md','%2e%2e/private'),null);
  assert.equal(resolveMarkdownFile('post.md','bad%00name'),null);
  assert.equal(resolveMarkdownFile('post.md','bad%ZZname'),null);
  for(const href of ['java\tscript:evil','jav&#97;script:evil','javascript&#58;evil','java&Tab;script:evil','java&NewLine;script:evil','\\\\host/x'])assert.equal(markdownTokenDestination(href).kind,'rejected',href);
  assert.deepEqual(markdownDestination('//example.test/x'),{kind:'external',href:'https://example.test/x'});
});
