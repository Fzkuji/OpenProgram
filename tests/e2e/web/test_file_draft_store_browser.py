from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
from threading import Thread

import pytest
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.browser


def _bundle_production_store(output: Path) -> None:
    entry = ROOT / "apps/web/lib/state/file-draft-store.ts"
    script = """
const esbuild = require("esbuild");
esbuild.buildSync({
  entryPoints: [process.argv[1]],
  bundle: true,
  format: "iife",
  globalName: "DraftStoreBundle",
  platform: "browser",
  target: "es2022",
  outfile: process.argv[2],
});
"""
    subprocess.run(
        ["node", "-e", script, str(entry), str(output)],
        cwd=ROOT,
        check=True,
    )


def test_production_indexeddb_draft_store_is_atomic_across_connections() -> None:
    with tempfile.TemporaryDirectory(prefix="openprogram-draft-browser-") as directory:
        bundle = Path(directory) / "file-draft-store.js"
        _bundle_production_store(bundle)
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self) -> None:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<title>draft-store</title>")

                def log_message(self, format: str, *args: object) -> None:
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                page.add_script_tag(path=str(bundle))
                result = page.evaluate(
                    """
                    async () => {
                      const { IndexedDbDraftStore, DraftStoreQuotaError, rebuildDraftIndexes } = DraftStoreBundle;
                      await new Promise((resolve) => {
                        const request = indexedDB.deleteDatabase(IndexedDbDraftStore.databaseName);
                        request.onsuccess = request.onerror = request.onblocked = () => resolve();
                      });
                      const first = new IndexedDbDraftStore();
                      const second = new IndexedDbDraftStore();
                      const add = (key) => (snapshot) => {
                        const drafts = [...snapshot.drafts, {
                          key, projectId: "p", path: key, draft: key,
                          baselineContent: "", baselineMtime: 1, bytes: 1, updatedAt: 1,
                        }];
                        return { drafts, indexes: rebuildDraftIndexes({ drafts, indexes: snapshot.indexes }) };
                      };
                      await Promise.all([first.mutate(add("a")), second.mutate(add("c"))]);
                      const concurrent = await first.load();
                      let abortName = null;
                      try {
                        await first.mutate(() => { throw new DraftStoreQuotaError(); });
                      } catch (error) {
                        abortName = error.name;
                      }
                      const retained = await second.load();
                      await first.mutate((snapshot) => ({
                        drafts: snapshot.drafts,
                        indexes: [{ projectId: "ghost", keys: ["p:missing"], count: 1, bytes: 1 }],
                      }));
                      const repaired = await second.repair();
                      return {
                        keys: concurrent.drafts.map((draft) => draft.key).sort(),
                        abortName,
                        retainedKeys: retained.drafts.map((draft) => draft.key).sort(),
                        repairedIndexes: repaired.indexes,
                      };
                    }
                    """
                )
                assert result == {
                    "keys": ["a", "c"],
                    "abortName": "QuotaExceededError",
                    "retainedKeys": ["a", "c"],
                    "repairedIndexes": [{
                        "projectId": "p",
                        "keys": ["a", "c"],
                        "count": 2,
                        "bytes": 2,
                    }],
                }
            finally:
                browser.close()
                server.shutdown()
                thread.join()
