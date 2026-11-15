"use client";

// The Scheduler's calendar view: Schedule-X draws the month / week / day /
// agenda grids; this file only turns tasks into entries for the visible
// range and routes clicks back to the page. Loaded with next/dynamic and
// ssr:false — Schedule-X renders into the DOM and needs `window`.

import "temporal-polyfill/global";
import "@schedule-x/theme-default/dist/index.css";

import { useEffect, useRef, useState } from "react";
import { ScheduleXCalendar, useNextCalendarApp } from "@schedule-x/react";
import {
  createViewDay,
  createViewMonthAgenda,
  createViewMonthGrid,
  createViewWeek,
  type CalendarEventExternal,
} from "@schedule-x/calendar";

import styles from "./scheduler-calendar.module.css";
import { calendarEntries, dateTimeLocalValue } from "./scheduler-view-model.mjs";

export interface SchedulerCalendarTask {
  id: string;
  title: string;
  type: string;
  enabled: boolean;
  cron?: string;
  run_at?: string;
}

interface CalendarEntry {
  id: string;
  taskId: string;
  title: string;
  type: string;
  enabled: boolean;
  start: Date;
  end: Date;
}

interface VisibleRange {
  start: Date;
  end: Date;
}

export interface SchedulerCalendarProps {
  tasks: SchedulerCalendarTask[];
  /** App locale; the parent remounts the calendar when it changes. */
  locale: string;
  onOpenTask: (taskId: string) => void;
  /** A `datetime-local` value for the new-task dialog's Run at field. */
  onCreateAt: (runAt: string) => void;
  /** Whether the per-task or total entry cap cut runs from the range. */
  onTruncatedChange?: (truncated: boolean) => void;
}

const VIEW_MONTH = createViewMonthGrid();
const VIEWS = [VIEW_MONTH, createViewWeek(), createViewDay(), createViewMonthAgenda()] as const;

function themeIsDark() {
  return typeof document !== "undefined"
    && document.documentElement.getAttribute("data-theme-mode") === "dark";
}

// Cron runs in the machine's wall-clock time, so the grid uses it too.
function localTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function toCalendarEvents(entries: CalendarEntry[], timeZone: string): CalendarEventExternal[] {
  return entries.map((entry) => ({
    id: entry.id,
    title: entry.title,
    start: Temporal.Instant.fromEpochMilliseconds(entry.start.getTime()).toZonedDateTimeISO(timeZone),
    end: Temporal.Instant.fromEpochMilliseconds(entry.end.getTime()).toZonedDateTimeISO(timeZone),
    taskId: entry.taskId,
    _options: {
      disableDND: true,
      disableResize: true,
      additionalClasses: ["op-scheduler-run"],
    },
  }));
}

export default function SchedulerCalendar({ tasks, locale, onOpenTask, onCreateAt, onTruncatedChange }: SchedulerCalendarProps) {
  const [timeZone] = useState(localTimeZone);
  // Schedule-X reads its config once; the callbacks reach the latest props
  // and tasks through refs.
  const latest = useRef({ tasks, onOpenTask, onCreateAt, onTruncatedChange });
  latest.current = { tasks, onOpenTask, onCreateAt, onTruncatedChange };
  const range = useRef<VisibleRange | null>(null);

  function eventsFor(visible: VisibleRange) {
    const { entries, truncated } = calendarEntries(latest.current.tasks, visible.start, visible.end) as {
      entries: CalendarEntry[];
      truncated: boolean;
    };
    latest.current.onTruncatedChange?.(truncated);
    return toCalendarEvents(entries, timeZone);
  }

  const calendar = useNextCalendarApp({
    views: [...VIEWS],
    defaultView: VIEW_MONTH.name,
    locale: locale === "zh" ? "zh-CN" : "en-US",
    firstDayOfWeek: locale === "zh" ? 1 : 7,
    timezone: timeZone as never,
    isDark: themeIsDark(),
    weekOptions: { gridHeight: 1440 },
    monthGridOptions: { nEventsPerDay: 4 },
    callbacks: {
      fetchEvents: async (next) => {
        // The range end is the last instant shown; entries use [start, end).
        range.current = {
          start: new Date(next.start.epochMilliseconds),
          end: new Date(next.end.epochMilliseconds + 1),
        };
        return eventsFor(range.current);
      },
      onEventClick: (event) => {
        if (typeof event.taskId === "string") latest.current.onOpenTask(event.taskId);
      },
      onClickDate: (date) => {
        latest.current.onCreateAt(dateTimeLocalValue(date.year, date.month, date.day));
      },
      onClickDateTime: (dateTime) => {
        latest.current.onCreateAt(dateTimeLocalValue(dateTime.year, dateTime.month, dateTime.day, dateTime.hour, dateTime.minute));
      },
    },
  });

  // Tasks changed (created, paused, deleted, filtered): redraw the range.
  useEffect(() => {
    if (!calendar || !range.current) return;
    calendar.events.set(eventsFor(range.current));
    // eventsFor reads everything it needs from refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [calendar, tasks]);

  // Follow the App's light/dark switch.
  useEffect(() => {
    if (!calendar) return;
    const sync = () => calendar.setTheme(themeIsDark() ? "dark" : "light");
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-theme-mode"] });
    return () => observer.disconnect();
  }, [calendar]);

  return (
    <div className={`${styles.calendar} flex flex-col flex-1 min-h-0 min-w-0`}>
      <ScheduleXCalendar calendarApp={calendar} />
    </div>
  );
}
