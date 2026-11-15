import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

import {
  calendarEmptyReason,
  calendarEntries,
  calendarTaskCount,
  coerceSchedulerView,
  cronOccurrences,
  dateTimeLocalValue,
  expandCronMacro,
  taskOccurrences,
} from "../../components/scheduler/scheduler-view-model.mjs";

// Local wall-clock dates: the worker matches cron against local time.
const local = (y, m, d, h = 0, min = 0) => new Date(y, m - 1, d, h, min);
const weekStart = local(2026, 11, 2); // Monday
const weekEnd = local(2026, 11, 9);

test("the stored scheduler view falls back to the list", () => {
  assert.equal(coerceSchedulerView("calendar"), "calendar");
  assert.equal(coerceSchedulerView("list"), "list");
  assert.equal(coerceSchedulerView(null), "list");
  assert.equal(coerceSchedulerView("grid"), "list");
});

test("cron macros expand like the worker and @reboot has no clock time", () => {
  assert.equal(expandCronMacro("@daily"), "0 0 * * *");
  assert.equal(expandCronMacro(" @Weekly "), "0 0 * * 0");
  assert.equal(expandCronMacro("@reboot"), null);
  assert.equal(expandCronMacro(""), null);
  assert.equal(expandCronMacro("0 9 * * 1-5"), "0 9 * * 1-5");
});

test("cron occurrences cover the range, including a run at its start", () => {
  const weekdays = cronOccurrences("0 9 * * 1-5", weekStart, weekEnd);
  assert.deepEqual(weekdays.map((d) => d.getDate()), [2, 3, 4, 5, 6]);
  assert.ok(weekdays.every((d) => d.getHours() === 9 && d.getMinutes() === 0));

  const midnight = cronOccurrences("@daily", weekStart, weekEnd);
  assert.equal(midnight.length, 7);
  assert.equal(midnight[0].getTime(), weekStart.getTime());
  assert.ok(midnight.every((d) => d < weekEnd));
});

test("cron occurrences respect the limit and reject what cannot run", () => {
  assert.equal(cronOccurrences("* * * * *", weekStart, weekEnd, 25).length, 25);
  assert.deepEqual(cronOccurrences("not a cron", weekStart, weekEnd), []);
  assert.deepEqual(cronOccurrences("0 0 9 * * 1", weekStart, weekEnd), []); // six fields
  assert.deepEqual(cronOccurrences("@reboot", weekStart, weekEnd), []);
  assert.deepEqual(cronOccurrences("0 9 * * *", weekEnd, weekStart), []);
});

test("task occurrences place one-time tasks at run_at and skip webhooks", () => {
  const inside = { id: "a", type: "once", run_at: local(2026, 11, 4, 15, 30).toISOString() };
  const outside = { id: "b", type: "once", run_at: local(2026, 12, 1, 9).toISOString() };
  assert.deepEqual(taskOccurrences(inside, weekStart, weekEnd).map((d) => d.getTime()), [local(2026, 11, 4, 15, 30).getTime()]);
  assert.deepEqual(taskOccurrences(outside, weekStart, weekEnd), []);
  assert.deepEqual(taskOccurrences({ id: "c", type: "once" }, weekStart, weekEnd), []);
  assert.deepEqual(taskOccurrences({ id: "d", type: "webhook" }, weekStart, weekEnd), []);
  assert.equal(taskOccurrences({ id: "e", type: "monitor", cron: "0 9 * * 1-5" }, weekStart, weekEnd).length, 5);
});

test("calendar entries map tasks to sorted, capped runs", () => {
  const tasks = [
    { id: "r", title: "Daily brief", type: "recurring", enabled: true, cron: "0 8 * * 1-5" },
    { id: "o", title: "Submit form", type: "once", enabled: false, run_at: local(2026, 11, 2, 7).toISOString() },
    { id: "w", title: "Deploy hook", type: "webhook", enabled: true },
  ];
  const { entries, truncated } = calendarEntries(tasks, weekStart, weekEnd);
  assert.equal(truncated, false);
  // The paused one-time task and the webhook place nothing.
  assert.equal(entries.length, 5);
  assert.ok(entries.every((entry) => entry.taskId === "r" && entry.enabled));
  assert.equal(entries[0].end - entries[0].start, 30 * 60_000);
  assert.equal(new Set(entries.map((entry) => entry.id)).size, entries.length);
  assert.ok(entries.every((entry, index) => index === 0 || entries[index - 1].start <= entry.start));

  const flood = calendarEntries(
    [{ id: "m", title: "Every minute", type: "monitor", enabled: true, cron: "* * * * *" }],
    weekStart, weekEnd, { perTask: 50 },
  );
  assert.equal(flood.entries.length, 50);
  assert.equal(flood.truncated, true);

  const many = calendarEntries(
    [1, 2, 3].map((n) => ({ id: String(n), title: `T${n}`, type: "recurring", enabled: true, cron: "0 * * * *" })),
    weekStart, weekEnd, { perTask: 100, total: 120 },
  );
  assert.equal(many.entries.length, 120);
  assert.equal(many.truncated, true);
});

test("calendar helpers count placeable tasks and format prefill times", () => {
  assert.equal(calendarTaskCount([{ type: "once" }, { type: "webhook" }, { type: "monitor" }]), 2);
  assert.equal(calendarTaskCount([{ type: "once", enabled: false }, { type: "monitor", enabled: true }]), 1);
  assert.equal(calendarTaskCount([{ type: "webhook" }]), 0);
  assert.equal(dateTimeLocalValue(2026, 3, 7), "2026-03-07T09:00");
  assert.equal(dateTimeLocalValue(2026, 12, 31, 18, 5), "2026-12-31T18:05");
});

test("Schedule-X and its signals share one Preact copy", () => {
  // The workspace also carries a Preact 11 beta for @pierre/trees; if
  // @preact/signals were hoisted next to it, the calendar's hooks would run
  // against a second Preact and never re-render.
  const fromWeb = createRequire(import.meta.url);
  const fromCalendar = createRequire(fromWeb.resolve("@schedule-x/calendar"));
  const fromSignals = createRequire(fromCalendar.resolve("@preact/signals"));
  assert.equal(fromSignals.resolve("preact"), fromCalendar.resolve("preact"));
  assert.match(fromCalendar("preact/package.json").version, /^10\./);
});

test("the calendar says why it is empty", () => {
  const once = { id: "a", type: "once", enabled: true, run_at: "2026-11-04T03:00:00+00:00" };
  assert.equal(calendarEmptyReason([once], [once], ""), null);
  assert.equal(calendarEmptyReason([], [], ""), "none");
  assert.equal(calendarEmptyReason([once], [], ""), "type");
  assert.equal(calendarEmptyReason([once], [], "zzz"), "search");
  assert.equal(calendarEmptyReason([], [], " "), "none");
});
