import fs from "node:fs";
import { selectResourceForHead } from "../components/right-sidebar/branches/resource-selection.ts";

const panel = fs.readFileSync(
  new URL("../components/right-sidebar/branches/index.tsx", import.meta.url),
  "utf8",
);
const item = fs.readFileSync(
  new URL("../components/right-sidebar/branches/branch-item.tsx", import.meta.url),
  "utf8",
);

for (const required of [
  "d.resource || cur[tid]?.resource || null",
  "t.resource as JobResourceView",
  "resourceForHead",
  "else if (!terminal)",
]) {
  if (!panel.includes(required)) throw new Error(`job resource wiring missing: ${required}`);
}
for (const required of ["<details", "<summary aria-label=", "resource: jobResource.resource", "execution: jobResource.execution"]) {
  if (!item.includes(required)) throw new Error(`visible job resource field missing: ${required}`);
}

const terminal = { status: "completed", finalHead: "head", resource: { resource_state: "released" }, updatedAt: 1 };
const running = { status: "running", targetHead: "head", resource: { resource_state: "live" }, updatedAt: 2 };
const completed = { ...running, status: "completed", resource: { resource_state: "released", queue_wait: null }, updatedAt: 3 };
if (selectResourceForHead({ old: terminal, current: running }, "head", "pending:")?.resource_state !== "live") {
  throw new Error("non-terminal job must win over stale terminal resource");
}
if (selectResourceForHead({ old: terminal, current: completed }, "head", "pending:")?.resource_state !== "released") {
  throw new Error("latest terminal job must win and match refreshed ordering");
}
