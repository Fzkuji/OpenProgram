/** Saved permission rules for ``computer_use``. A rule names the key a
 *  call is remembered under: ``window <application>``, ``screen``,
 *  ``browser <browser>`` (``browser chrome``, ``browser safari``, ...) or
 *  ``<device> <type>``; ``:*`` and globs widen it. */
import { USER_BROWSERS } from "./browsers.ts";

export type ComputerUseRule =
  | { kind: "window"; app: string }
  | { kind: "screen" }
  | { kind: "browser"; browser: string }
  | { kind: "device"; machine: string; surface: string };

const RULE = /^computer_use\((.+)\)$/i;
const WIDE = /[*?[]/;
const TYPES = new Set(["window", "screen", "browser"]);

/** What a ``computer_use(<key>)`` rule names, or null for any other rule. */
export function parseComputerUseRule(rule: string): ComputerUseRule | null {
  const match = RULE.exec(rule.trim());
  if (!match) return null;
  const key = match[1].trim().replace(/:\*$/, " *");
  const words = key.split(/\s+/);
  if (words[0] === "window" && words.length > 1) return { kind: "window", app: words.slice(1).join(" ") };
  if (key === "screen") return { kind: "screen" };
  if (words.length === 2 && words[0] === "browser" && USER_BROWSERS[words[1]]) return { kind: "browser", browser: words[1] };
  if (words.length > 1 && TYPES.has(words[1])) {
    return { kind: "device", machine: words[0], surface: words.slice(1).join(" ") };
  }
  return null;
}

/** The application a ``window <application>`` rule allows, or null for any other rule and for a pattern. */
export function computerUseRuleApp(rule: string): string | null {
  const parsed = parseComputerUseRule(rule);
  return parsed?.kind === "window" && !WIDE.test(parsed.app) ? parsed.app : null;
}
