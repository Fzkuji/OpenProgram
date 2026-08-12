import fs from "node:fs";

const panel = fs.readFileSync(
  new URL("../components/right-sidebar/branches/index.tsx", import.meta.url),
  "utf8",
);
const item = fs.readFileSync(
  new URL("../components/right-sidebar/branches/branch-item.tsx", import.meta.url),
  "utf8",
);

for (const required of ["d.resource || null", "t.resource as Record", "resourceForHead"]) {
  if (!panel.includes(required)) throw new Error(`task resource wiring missing: ${required}`);
}
for (const required of ["<details", "<summary aria-label=", "resource_state", "reason_code", "capacity", "budget"]) {
  if (!item.includes(required)) throw new Error(`visible task resource field missing: ${required}`);
}
