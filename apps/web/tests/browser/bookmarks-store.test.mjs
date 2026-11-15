import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";

import { installLoader } from "../_support/load.mjs";

installLoader();

// The store reads `window` and `localStorage` directly; a bare EventTarget
// and a Map-backed storage that can be told to reject writes are enough.
const storage = new Map();
const windowEvents = new EventTarget();
let failWrites = false;
globalThis.window = windowEvents;
globalThis.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => {
    if (failWrites) throw new Error("storage disabled");
    storage.set(key, value);
  },
  removeItem: (key) => storage.delete(key),
};

const bookmarks = await import("@/lib/tabs/browser/bookmarks");
const { BOOKMARKS_STORAGE_KEY: KEY, BOOKMARKS_ROOT_ID: ROOT, BOOKMARKS_VERSION: VERSION } = bookmarks;

let changes = 0;
windowEvents.addEventListener(bookmarks.BOOKMARKS_CHANGE_EVENT, () => changes++);

const first = { title: "Example", url: "https://example.com/" };
const second = { title: "OpenProgram", url: "https://openprogram.ai/" };

function store(value) {
  storage.set(KEY, typeof value === "string" ? value : JSON.stringify(value));
  changes = 0;
}
function storeTree(children) {
  store({ version: VERSION, root: { kind: "folder", id: ROOT, title: "", children } });
}
/** The stored tree's bookmark rows, as {title,url}. */
function storedBookmarks() {
  const parsed = JSON.parse(storage.get(KEY));
  assert.equal(parsed.version, VERSION, "the stored tree carries a version");
  return bookmarks.flattenBookmarks(parsed.root);
}

beforeEach(() => {
  storage.clear();
  failWrites = false;
  changes = 0;
});

describe("flat bookmark API", () => {
  test("empty or corrupt storage reads as no bookmarks", () => {
    assert.deepEqual(bookmarks.readBookmarks(), []);
    store("not-json");
    assert.deepEqual(bookmarks.readBookmarks(), []);
  });

  test("toggle adds then removes, saving a versioned tree and announcing each change", () => {
    assert.deepEqual(bookmarks.toggleBookmark(first), [first]);
    assert.deepEqual(storedBookmarks(), [first]);
    assert.equal(changes, 1);
    assert.deepEqual(bookmarks.toggleBookmark(first), []);
    assert.deepEqual(storedBookmarks(), []);
  });

  test("toggle over corrupt storage starts a fresh list and appends in order", () => {
    store("[");
    assert.deepEqual(bookmarks.toggleBookmark(first), [first]);
    assert.deepEqual(bookmarks.toggleBookmark(second), [first, second]);
    assert.deepEqual(storedBookmarks(), [first, second]);
    assert.deepEqual(bookmarks.readBookmarks(), [first, second]);
    assert.equal(changes, 2);
    assert.deepEqual(bookmarks.removeBookmark(first.url), [second]);
    assert.deepEqual(storedBookmarks(), [second]);
    assert.equal(changes, 3);
  });

  test("rename trims, falls back to the url when blank, and ignores unknown urls", () => {
    bookmarks.toggleBookmark(second);
    changes = 0;
    assert.deepEqual(bookmarks.renameBookmark(second.url, "  Renamed  "), [{ title: "Renamed", url: second.url }]);
    assert.deepEqual(bookmarks.renameBookmark(second.url, "   "), [{ title: second.url, url: second.url }]);
    assert.equal(changes, 2);
    assert.deepEqual(bookmarks.renameBookmark("https://missing.example/", "Missing"), [{ title: second.url, url: second.url }]);
    assert.equal(changes, 2);
  });

  test("a rejected write keeps the stored list and announces nothing", () => {
    bookmarks.toggleBookmark(second);
    changes = 0;
    failWrites = true;
    let failedRename;
    assert.doesNotThrow(() => { failedRename = bookmarks.renameBookmark(second.url, "Failed"); });
    assert.deepEqual(failedRename, [second]);
    assert.doesNotThrow(() => bookmarks.toggleBookmark(first));
    assert.deepEqual(bookmarks.toggleBookmark(first), [second]);
    assert.deepEqual(storedBookmarks(), [second]);
    assert.equal(changes, 0);
  });
});

describe("migration from the v1 flat array", () => {
  test("the legacy array becomes the root folder with identified bookmark nodes", () => {
    store([first, second]);
    const migrated = bookmarks.readBookmarkTree();
    assert.equal(migrated.kind, "folder");
    assert.equal(migrated.id, ROOT);
    assert.equal(migrated.children.length, 2);
    assert.ok(migrated.children.every((node) => node.kind === "bookmark" && typeof node.id === "string"));
    assert.deepEqual(bookmarks.flattenBookmarks(migrated), [first, second]);
    assert.deepEqual(bookmarks.readBookmarks(), [first, second]);
  });

  test("junk legacy rows are dropped and a written tree round-trips", () => {
    store([first, { title: "no url" }, null, "nope", second]);
    assert.deepEqual(bookmarks.readBookmarks(), [first, second]);
    const kept = { title: "Kept", url: "https://kept.example/" };
    bookmarks.toggleBookmark(kept);
    assert.deepEqual(bookmarks.readBookmarks(), [first, second, kept]);
  });
});

describe("bookmark bar layout", () => {
  test("the bar shows the Bookmarks bar folder's children and loose root items; non-empty other roots trail", () => {
    const loose = { kind: "bookmark", id: "loose", title: "Loose", url: "https://loose.example/" };
    const bar = {
      kind: "folder", id: "bar", title: "Bookmarks bar",
      children: [
        { kind: "bookmark", id: "github", title: "GitHub", url: "https://github.com/" },
        { kind: "folder", id: "research", title: "Research", children: [] },
      ],
    };
    const other = {
      kind: "folder", id: "other", title: "Other bookmarks",
      children: [{ kind: "bookmark", id: "docs", title: "Docs", url: "https://docs.example/" }],
    };
    const mobile = { kind: "folder", id: "mobile", title: "Mobile bookmarks", children: [] };
    const layout = bookmarks.bookmarkBarLayout({ kind: "folder", id: ROOT, title: "", children: [bar, other, mobile, loose] });
    assert.deepEqual(layout.items, [...bar.children, loose]);
    assert.deepEqual(layout.trailingFolders, [other]);
  });

  test("menu entries list nested bookmarks with their folder path", () => {
    assert.deepEqual(
      bookmarks.bookmarkMenuEntries({
        kind: "folder", id: "folder", title: "Folder",
        children: [{
          kind: "folder", id: "nested", title: "Nested",
          children: [{ kind: "bookmark", id: "paper", title: "Paper", url: "https://paper.example/" }],
        }],
      }),
      [{ title: "Paper", url: "https://paper.example/", path: ["Nested", "Paper"] }],
    );
  });
});

describe("importing a browser's bookmark tree", () => {
  const LEGACY_TITLE = "Imported from Google Chrome · Person 1";

  test("the source folder tree is preserved and merged into same-name roots; unsafe urls are skipped", () => {
    storeTree([
      { kind: "folder", id: "existing-bar", title: "Bookmarks bar", children: [] },
      {
        kind: "folder", id: "legacy-import", title: LEGACY_TITLE,
        children: [{ kind: "bookmark", id: "legacy-paper", title: "Paper", url: "https://paper.example/" }],
      },
    ]);
    const source = [
      {
        kind: "folder", title: "Bookmarks bar",
        children: [{
          kind: "folder", title: "Research",
          children: [
            { kind: "bookmark", title: "Paper", url: "https://paper.example" },
            { kind: "bookmark", title: "Unsafe", url: "javascript:alert(1)" },
          ],
        }],
      },
      { kind: "folder", title: "Other bookmarks", children: [{ kind: "bookmark", title: "Docs", url: "https://docs.example" }] },
    ];
    assert.equal(bookmarks.importBookmarkTree(source, LEGACY_TITLE), 2);
    assert.equal(changes, 1, "one import writes the merged tree once");
    const tree = bookmarks.readBookmarkTree();
    const bar = tree.children.find((node) => node.title === "Bookmarks bar");
    assert.equal(bar.id, "existing-bar", "same-name source roots merge instead of duplicating");
    assert.equal(bar.children[0].title, "Research");
    assert.equal(bar.children[0].children[0].url, "https://paper.example/");
    assert.equal(bar.children[0].children[0].id, "legacy-paper", "the legacy import is moved, not duplicated");
    assert.equal(tree.children[1].title, "Other bookmarks");
    assert.equal(tree.children[1].children[0].url, "https://docs.example/");
    assert.equal(tree.children.some((node) => node.id === "legacy-import"), false, "the emptied legacy folder is removed");

    assert.equal(bookmarks.importBookmarkTree([source[0]]), 0, "re-importing the same tree is a no-op");
    assert.equal(changes, 1);
  });

  test("a same-title folder with user-made nesting is left alone", () => {
    storeTree([{
      kind: "folder", id: "user-import-title", title: LEGACY_TITLE,
      children: [{
        kind: "folder", id: "user-personal", title: "Personal",
        children: [{ kind: "bookmark", id: "user-paper", title: "Paper", url: "https://paper.example/" }],
      }],
    }]);
    const count = bookmarks.importBookmarkTree([
      { kind: "folder", title: "Bookmarks bar", children: [{ kind: "bookmark", title: "Paper", url: "https://paper.example" }] },
    ], LEGACY_TITLE);
    assert.equal(count, 0);
    assert.equal(changes, 0);
    assert.equal(bookmarks.findNode(bookmarks.readBookmarkTree(), "user-personal").children[0].id, "user-paper");
  });

  test("an empty same-title folder is kept as user data", () => {
    storeTree([{ kind: "folder", id: "user-empty-import-title", title: LEGACY_TITLE, children: [] }]);
    assert.equal(bookmarks.importBookmarkTree([], LEGACY_TITLE), 0);
    assert.equal(changes, 0);
    assert.ok(bookmarks.findNode(bookmarks.readBookmarkTree(), "user-empty-import-title"));
  });

  test("existing legacy urls are canonicalised the same way as incoming ones", () => {
    store([{ title: "Canonical", url: "https://canonical.example" }]);
    assert.equal(bookmarks.importBookmarkTree([
      { kind: "bookmark", title: "Canonical again", url: "https://canonical.example/" },
    ]), 0);
    assert.equal(changes, 0);
    assert.equal(bookmarks.readBookmarks().length, 1);
  });

  test("same-title source siblings stay distinct, also on a repeated import", () => {
    storeTree([]);
    const source = [
      { kind: "folder", title: "Same", children: [{ kind: "bookmark", title: "One", url: "https://one.example" }] },
      { kind: "folder", title: "Same", children: [{ kind: "bookmark", title: "Two", url: "https://two.example" }] },
    ];
    assert.equal(bookmarks.importBookmarkTree(source), 2);
    assert.deepEqual(
      bookmarks.readBookmarkTree().children.map((folder) => folder.children[0].url),
      ["https://one.example/", "https://two.example/"],
    );
    assert.equal(bookmarks.importBookmarkTree(source), 0);
    assert.equal(bookmarks.readBookmarkTree().children.length, 2);
  });

  test("a rejected write throws instead of reporting imported bookmarks", () => {
    storeTree([]);
    failWrites = true;
    assert.throws(
      () => bookmarks.importBookmarkTree([{ kind: "bookmark", title: "Not saved", url: "https://not-saved.example" }]),
      /bookmark storage unavailable/,
    );
    failWrites = false;
    assert.equal(changes, 0);
    assert.deepEqual(bookmarks.readBookmarks(), []);
  });
});

describe("folder operations", () => {
  function setup() {
    store([first, second]);
    const work = bookmarks.createFolder("  Work  ").children.find((node) => node.kind === "folder");
    const firstId = bookmarks.readBookmarkTree().children.find((node) => node.url === first.url).id;
    changes = 0;
    return { work, firstId };
  }

  test("createFolder trims the title, falls back to a default name, and ignores a missing parent", () => {
    store([first, second]);
    const work = bookmarks.createFolder("  Work  ").children.find((node) => node.kind === "folder");
    assert.equal(changes, 1);
    assert.equal(work.title, "Work");
    assert.deepEqual(work.children, []);
    assert.ok(bookmarks.createFolder("   ").children.some((node) => node.title === "New folder"));
    changes = 0;
    bookmarks.createFolder("Nowhere", "missing-id");
    assert.equal(changes, 0);
  });

  test("moving a bookmark into a folder nests it without losing it from the flat view", () => {
    const { work, firstId } = setup();
    bookmarks.moveNode(firstId, work.id);
    const tree = bookmarks.readBookmarkTree();
    const workNow = bookmarks.findNode(tree, work.id);
    assert.equal(workNow.children.length, 1);
    assert.equal(workNow.children[0].url, first.url);
    assert.ok(!tree.children.some((node) => node.kind === "bookmark" && node.url === first.url));
    assert.deepEqual(bookmarks.readBookmarks().map((b) => b.url).sort(), [first.url, second.url].sort());

    bookmarks.moveNode(firstId, ROOT, 0);
    const back = bookmarks.readBookmarkTree();
    assert.equal(back.children[0].url, first.url, "index 0 places the node first");
    assert.equal(bookmarks.findNode(back, work.id).children.length, 0);
  });

  test("moving among siblings reorders them; the index is counted after the node is taken out", () => {
    setup();
    const ids = bookmarks.readBookmarkTree().children.map((node) => node.id);
    assert.ok(ids.length >= 3);
    const [topId, midId] = ids;
    // The drop zone between rows 2 and 3 is index 2; a downward move lands at 2 - 1.
    bookmarks.moveNode(topId, ROOT, 1);
    assert.deepEqual(bookmarks.readBookmarkTree().children.slice(0, 2).map((node) => node.id), [midId, topId]);
    bookmarks.moveNode(topId, ROOT, 0);
    assert.equal(bookmarks.readBookmarkTree().children[0].id, topId);
    assert.deepEqual(bookmarks.readBookmarkTree().children.map((node) => node.id).sort(), [...ids].sort());
  });

  test("rename trims folders and bookmarks; blank keeps a folder's name and gives a bookmark its url", () => {
    const { work, firstId } = setup();
    bookmarks.renameNode(work.id, "  Reading  ");
    assert.equal(bookmarks.findNode(bookmarks.readBookmarkTree(), work.id).title, "Reading");
    bookmarks.renameNode(work.id, "   ");
    assert.equal(bookmarks.findNode(bookmarks.readBookmarkTree(), work.id).title, "Reading");
    bookmarks.renameNode(firstId, "   ");
    assert.equal(bookmarks.findNode(bookmarks.readBookmarkTree(), firstId).title, first.url);
    changes = 0;
    bookmarks.renameNode(ROOT, "Root");
    bookmarks.renameNode("missing-id", "Nope");
    assert.equal(changes, 0, "the root and unknown ids are not renameable");
  });

  test("a folder never moves into itself or its own subtree, and the root never moves", () => {
    const { work, firstId } = setup();
    const nestedId = bookmarks.findNode(bookmarks.createFolder("Nested", work.id), work.id).children[0].id;
    changes = 0;
    bookmarks.moveNode(work.id, nestedId);
    bookmarks.moveNode(work.id, work.id);
    bookmarks.moveNode(ROOT, work.id);
    bookmarks.moveNode("missing-id", work.id);
    bookmarks.moveNode(firstId, "missing-parent");
    assert.equal(changes, 0);
    assert.ok(bookmarks.findNode(bookmarks.readBookmarkTree(), nestedId), "the subtree survived");
  });

  test("deleting a folder takes its subtree; the root is not deletable", () => {
    const { work, firstId } = setup();
    const nestedId = bookmarks.findNode(bookmarks.createFolder("Nested", work.id), work.id).children[0].id;
    bookmarks.moveNode(firstId, nestedId);
    bookmarks.deleteNode(work.id);
    const tree = bookmarks.readBookmarkTree();
    assert.equal(bookmarks.findNode(tree, work.id), null);
    assert.equal(bookmarks.findNode(tree, nestedId), null);
    assert.deepEqual(bookmarks.readBookmarks(), [second]);
    changes = 0;
    bookmarks.deleteNode(ROOT);
    bookmarks.deleteNode("missing-id");
    assert.equal(changes, 0);
    assert.deepEqual(bookmarks.readBookmarks(), [second]);
  });

  test("folder operations survive a rejected write without throwing", () => {
    const { firstId } = setup();
    failWrites = true;
    assert.doesNotThrow(() => bookmarks.createFolder("Doomed"));
    assert.doesNotThrow(() => bookmarks.deleteNode(firstId));
    assert.equal(changes, 0);
  });
});

test("subscribers refresh on the change event and on storage writes to the bookmarks key only", () => {
  let refreshes = 0;
  const unsubscribe = bookmarks.subscribeBookmarks(() => refreshes++);
  windowEvents.dispatchEvent(new Event(bookmarks.BOOKMARKS_CHANGE_EVENT));
  assert.equal(refreshes, 1);
  const storageEvent = (key) => Object.defineProperty(new Event("storage"), "key", { value: key });
  windowEvents.dispatchEvent(storageEvent(KEY));
  assert.equal(refreshes, 2);
  windowEvents.dispatchEvent(storageEvent("unrelated.key"));
  assert.equal(refreshes, 2);
  unsubscribe();
  windowEvents.dispatchEvent(new Event(bookmarks.BOOKMARKS_CHANGE_EVENT));
  windowEvents.dispatchEvent(storageEvent(KEY));
  assert.equal(refreshes, 2, "unsubscribe removes both listeners");
});
