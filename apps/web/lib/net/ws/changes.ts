/** A window reads again when the Worker says something changed, not on a
 *  timer. The Worker sends `{type: "changed", topic, keys}` frames
 *  (openprogram/host/_platform/changes.py); `keys` names what changed (a
 *  conversation, a job) or is null for "anything under this topic". Every
 *  topic counts as changed once the socket reconnects, since frames sent
 *  while it was down are lost. A hidden window holds a change and reads once
 *  it shows again. */
import { useEffect, useRef } from "react";
import { onChanged, type Handler } from "@/lib/net/ws/change-bus";

export { dispatchChanged, dispatchReconnected, onChanged, onReconnected } from "@/lib/net/ws/change-bus";

function hidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

/** Run `refresh` when any of `topics` changes for one of `keys` (or for
 *  anything when `keys` is omitted). While the window is hidden the change is
 *  held, and `refresh` runs once when it shows. Changes that arrive while a
 *  refresh is running cause one more after it. */
export function useChanged(
  topics: string | readonly string[],
  refresh: () => unknown,
  keys?: readonly string[] | null,
): void {
  const latest = useRef(refresh);
  latest.current = refresh;
  const keyList = keys ? [...keys].sort().join("\n") : null;
  const topicList = (typeof topics === "string" ? [topics] : [...topics]).join("\n");
  useEffect(() => {
    const wanted = keyList === null ? null : new Set(keyList ? keyList.split("\n") : []);
    let held = false;
    let running = false;
    let again = false;
    let disposed = false;
    const run = async () => {
      if (disposed) return;
      if (hidden()) { held = true; return; }
      if (running) { again = true; return; }
      running = true;
      try {
        await latest.current();
      } catch {
        /* the next change reads again */
      }
      running = false;
      if (again && !disposed) { again = false; void run(); }
    };
    const onChange: Handler = changed => {
      if (wanted !== null && changed !== null && !changed.some(key => wanted.has(key))) return;
      void run();
    };
    const offs = topicList.split("\n").map(topic => onChanged(topic, onChange));
    const onVisible = () => {
      if (held && !hidden()) { held = false; void run(); }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      disposed = true;
      offs.forEach(off => off());
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [topicList, keyList]);
}
