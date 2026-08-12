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
  "t.resource as Record",
  "resourceForHead",
  "if (!terminal) runningHeads.add(synth)",
]) {
  if (!panel.includes(required)) throw new Error(`task resource wiring missing: ${required}`);
}
for (const required of ["<details", "<summary aria-label=", "resource_state", "reason_code", "capacity", "budget"]) {
  if (!item.includes(required)) throw new Error(`visible task resource field missing: ${required}`);
}

const terminal = { status: "completed", finalHead: "head", resource: { resource_state: "released" }, updatedAt: 1 };
const running = { status: "running", targetHead: "head", resource: { resource_state: "live" }, updatedAt: 2 };
const completed = { ...running, status: "completed", resource: { resource_state: "released", reason_code: "completed" }, updatedAt: 3 };
if (selectResourceForHead({ old: terminal, current: running }, "head", "pending:")?.resource_state !== "live") {
  throw new Error("non-terminal task must win over stale terminal resource");
}
if (selectResourceForHead({ old: terminal, current: completed }, "head", "pending:")?.reason_code !== "completed") {
  throw new Error("latest terminal task must win and match refreshed ordering");
}
