// Rule: the Schedule-X calendar touches the DOM, so it is a "use client" module
// loaded with dynamic(..., { ssr: false }); it never renders on the server.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../../components/scheduler/${path}`, import.meta.url), "utf8");
const page = read("scheduler-page.tsx");
const calendar = read("scheduler-calendar.tsx");

assert.match(page, /dynamic\(\(\) => import\("\.\/scheduler-calendar"\), \{\s*ssr: false/, "the calendar is imported with ssr: false");
assert.match(calendar, /^"use client";/, "scheduler-calendar.tsx starts with \"use client\"");
assert.match(calendar, /@schedule-x\/react/);
// Hover help goes through HoverTip, never a native title attribute.
assert.doesNotMatch(calendar, /\btitle=/);

console.log("scheduler checks passed");
