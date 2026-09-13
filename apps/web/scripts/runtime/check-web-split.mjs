import { readDesktopBridgeSource } from "../testing/feature-source.mjs";

import assert from "node:assert/strict";

import { readFile } from "node:fs/promises";

import { existsSync } from "node:fs";

import { registerHooks } from "node:module";

import { fileURLToPath } from "node:url";


import { readCenterTabStripSource } from "../tabs/center-tab-strip-source.mjs";
import { installFixtures } from "./web-split-checks/fixtures.mjs";
const testContext = { readDesktopBridgeSource, assert, readFile, existsSync, registerHooks, fileURLToPath, readCenterTabStripSource, sourceUrl: import.meta.url };
installFixtures(testContext);
await (await import("./web-split-checks/state-snapshots.mjs")).run(testContext);
await (await import("./web-split-checks/transfer-placement.mjs")).run(testContext);
await (await import("./web-split-checks/journal-recovery.mjs")).run(testContext);
await (await import("./web-split-checks/geometry-scheduling.mjs")).run(testContext);
await (await import("./web-split-checks/layout-contracts.mjs")).run(testContext);
await (await import("./web-split-checks/pip-ownership.mjs")).run(testContext);
await (await import("./web-split-checks/navigation.mjs")).run(testContext);
await (await import("./web-split-checks/surface-inventory.mjs")).run(testContext);
await (await import("./web-split-checks/transfer-destination.mjs")).run(testContext);
await (await import("./web-split-checks/transfer-source.mjs")).run(testContext);
await (await import("./web-split-checks/detached-recovery.mjs")).run(testContext);
