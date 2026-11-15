import assert from "node:assert/strict";
import test from "node:test";

const { lookupFilePaths, knownFilePath, resetFilePathLookup } = await import("../../lib/files/file-path-lookup.ts");

function worker(files) {
  const requests = [];
  const fetch = async (url) => {
    const params = new URL(url, "http://worker").searchParams;
    requests.push({ paths: params.getAll("path"), session: params.get("session_id") });
    const answer = Object.fromEntries(params.getAll("path").map(path => [path, files[path] ?? null]));
    return new Response(JSON.stringify({ files: answer }), { status: 200 });
  };
  return { fetch, requests };
}

test("the paths of every reply on screen go out in one request per chat", async () => {
  const { fetch, requests } = worker({ "a.pptx": "/w/a.pptx", "/w/b.pdf": "/w/b.pdf" });
  resetFilePathLookup(fetch);
  const [first, second] = await Promise.all([
    lookupFilePaths("s1", ["a.pptx", "missing.docx"]),
    lookupFilePaths("s1", ["/w/b.pdf", "a.pptx"]),
  ]);
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0], { paths: ["a.pptx", "missing.docx", "/w/b.pdf"], session: "s1" });
  assert.equal(first.get("a.pptx"), "/w/a.pptx");
  assert.equal(first.get("missing.docx"), null);
  assert.equal(second.get("/w/b.pdf"), "/w/b.pdf");
});

test("an answer is kept: scrolling back asks nothing, a missing file is asked again later", async () => {
  const { fetch, requests } = worker({ "a.pptx": "/w/a.pptx" });
  resetFilePathLookup(fetch);
  await lookupFilePaths("s1", ["a.pptx", "later.pdf"]);
  await lookupFilePaths("s1", ["a.pptx", "later.pdf"]);
  assert.equal(requests.length, 1);
  assert.equal(knownFilePath("s1", "a.pptx", Date.now() + 60_000), "/w/a.pptx");
  assert.equal(knownFilePath("s1", "later.pdf", Date.now() + 60_000), undefined);
  assert.equal(knownFilePath("s2", "a.pptx"), undefined, "answers belong to one chat");
});

test("many paths are split into requests of 64; a failing worker leaves them all plain", async () => {
  const { fetch, requests } = worker({});
  resetFilePathLookup(fetch);
  const paths = Array.from({ length: 70 }, (_, i) => `f${i}.pdf`);
  await lookupFilePaths("s1", paths);
  assert.deepEqual(requests.map(r => r.paths.length), [64, 6]);
  resetFilePathLookup(async () => { throw new Error("offline"); });
  const answers = await lookupFilePaths("s1", ["x.pdf"]);
  assert.equal(answers.get("x.pdf"), null);
});
