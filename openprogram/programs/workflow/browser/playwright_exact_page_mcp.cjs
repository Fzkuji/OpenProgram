#!/usr/bin/env node
"use strict";

const { createConnection } = require("@playwright/mcp");
const { chromium } = require("playwright");
const { StdioServerTransport } = require("playwright-core/lib/utilsBundle");

const endpoint = process.argv[2];
const expectedTargetId = process.argv[3];

function filteredContext(context, exactPage) {
  const pageListeners = new Map();
  return new Proxy(context, {
    get(target, property) {
      if (property === "pages") return () => [exactPage];
      if (property === "newPage") {
        return async () => { throw new Error("new Page is disabled for exact-Page control"); };
      }
      if (["on", "once", "addListener"].includes(property)) {
        return (event, listener) => {
          if (event !== "page") {
            target[property](event, listener);
            return target;
          }
          const wrapped = (page) => { if (page === exactPage) listener(page); };
          pageListeners.set(listener, wrapped);
          target[property](event, wrapped);
          return target;
        };
      }
      if (["off", "removeListener"].includes(property)) {
        return (event, listener) => {
          target[property](event, pageListeners.get(listener) || listener);
          pageListeners.delete(listener);
          return target;
        };
      }
      const value = Reflect.get(target, property, target);
      return typeof value === "function" ? value.bind(target) : value;
    },
  });
}

async function main() {
  if (!endpoint || !expectedTargetId) throw new Error("endpoint and target id are required");
  const browser = await chromium.connectOverCDP(endpoint);
  let exactPage;
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      const session = await context.newCDPSession(page);
      try {
        const { targetInfo } = await session.send("Target.getTargetInfo");
        if (targetInfo && targetInfo.targetId === expectedTargetId) {
          exactPage = page;
          break;
        }
      } finally {
        await session.detach().catch(() => {});
      }
    }
    if (exactPage) break;
  }
  if (!exactPage) throw new Error("exact Page target was not found");

  const context = filteredContext(exactPage.context(), exactPage);
  const server = await createConnection(
    { capabilities: ["core", "vision"] },
    async () => context,
  );
  await server.connect(new StdioServerTransport());

  const shutdown = async () => {
    await server.close().catch(() => {});
    process.exit(0);
  };
  process.stdin.once("end", shutdown);
  process.once("SIGINT", shutdown);
  process.once("SIGTERM", shutdown);
}

main().catch((error) => {
  process.stderr.write(`${error && error.stack ? error.stack : error}\n`);
  process.exitCode = 1;
});
