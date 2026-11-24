/** Reading a ``computer_use`` call: where it lands and what it does.
 *
 *  A call names where it lands with ``device`` (left out for this computer),
 *  ``type`` (window, screen or browser) and, for a browser, ``app`` (chrome,
 *  safari, firefox, ... as browsers.ts lists them; left out for OpenProgram's
 *  own). Every call is read here and
 *  nowhere else: the rows, the group summaries, the Activity pages and the
 *  approval card take the target and a flat step in the present names of the
 *  tool's vocabulary
 *  (openprogram/programs/tools/computer_use/computer_use/vocabulary.py).
 *  Stored history keeps the names a model wrote, so the names that table
 *  rewrites are read here too and shown in the present wording. */
import { browserOf } from "./browsers.ts";

export type Args = Record<string, unknown>;

function record(value: unknown): Args {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Args : {};
}

function word(value: unknown): string {
  return typeof value === "string" ? value.trim() : typeof value === "number" ? String(value) : "";
}

export interface CallTarget {
  /** The target's key as the worker writes it: "window", "browser chrome", "lab screen". */
  target: string;
  /** "window" | "screen" | "browser", or "" for a call without a type it knows. */
  family: string;
  /** The user's own browser a browser call drives ("chrome", "safari", ...); "" for OpenProgram's own. */
  browser: string;
  /** The device's id, "" for this computer. */
  machine: string;
}

const TYPES = new Set(["window", "screen", "browser"]);
const THIS_COMPUTER = new Set(["", "local", "this computer", "this_computer", "localhost"]);

/** Where a call lands, matched without regard to case. */
export function callTarget(args: Args): CallTarget {
  const type = word(args.type).toLowerCase();
  const family = TYPES.has(type) ? type : "";
  const device = word(args.device);
  const machine = THIS_COMPUTER.has(device.toLowerCase()) ? "" : device.toLowerCase();
  const browser = family === "browser" ? browserOf(word(args.app)) : "";
  return { target: [machine, family, browser].filter(Boolean).join(" "), family, browser, machine };
}

const machineNames = new Map<string, string>();

/** The names of the machines set up now, so a call shows "Lab VM" rather than "lab". */
export function rememberMachineNames(machines: Iterable<{ id: string; name?: string | null }>): void {
  machineNames.clear();
  for (const machine of machines) {
    const name = typeof machine.name === "string" ? machine.name.trim() : "";
    if (machine.id && name) machineNames.set(machine.id.toLowerCase(), name);
  }
}

/** A machine's name where it is known, else its id. */
export function machineLabel(id: string): string {
  return machineNames.get(id.toLowerCase()) ?? id;
}

// Names carried over from other tools: [family or "*", name] -> [our action, the arguments it implies].
const ACTIONS: Record<string, [string, Args]> = {
  "*:screenshot": ["get_state", { include: "screenshot" }],
  "*:take_screenshot": ["get_state", { include: "screenshot" }],
  "*:get_app_state": ["get_state", {}],
  "*:read": ["get_state", {}],
  "*:snapshot": ["get_state", {}],
  "*:read_page": ["get_state", {}],
  "*:observe": ["get_state", {}],
  "*:left_click": ["click", {}],
  "*:tap": ["click", {}],
  "*:double_click": ["click", { click_count: 2 }],
  "*:triple_click": ["click", { click_count: 3 }],
  "*:right_click": ["click", { mouse_button: "right" }],
  "*:middle_click": ["click", { mouse_button: "middle" }],
  "*:long_press": ["click", { seconds: 1 }],
  "*:type": ["type_text", {}],
  "*:write": ["type_text", {}],
  "*:input_text": ["type_text", {}],
  "*:press": ["press_key", {}],
  "*:key": ["press_key", {}],
  "*:keypress": ["press_key", {}],
  "*:hotkey": ["press_key", {}],
  "*:scroll_at": ["scroll", {}],
  "*:left_click_drag": ["drag", {}],
  "*:drag_and_drop": ["drag", {}],
  "*:move": ["hover", {}],
  "*:mouse_move": ["hover", {}],
  "*:move_to": ["hover", {}],
  "*:sleep": ["wait", {}],
  "*:fill": ["set_value", {}],
  "*:select": ["set_value", {}],
  "*:select_option": ["set_value", {}],
  "window:open_app": ["get_state", {}],
  "window:launch_app": ["get_state", {}],
  "browser:tabs": ["list_tabs", {}],
  "browser:list_pages": ["list_tabs", {}],
  "browser:open": ["create_tab", {}],
  "browser:new_tab": ["create_tab", {}],
  "browser:new_page": ["create_tab", {}],
  "browser:close": ["close_tab", {}],
  "browser:close_page": ["close_tab", {}],
  "browser:goto": ["navigate", {}],
  "browser:navigate_page": ["navigate", {}],
  "browser:go_back": ["back", {}],
  "browser:go_forward": ["forward", {}],
  "browser:upload": ["upload_file", {}],
  "browser:console": ["read_console_messages", {}],
  "browser:console_messages": ["read_console_messages", {}],
  "browser:network": ["read_network_requests", {}],
  "browser:network_requests": ["read_network_requests", {}],
  "browser:panes": ["list_panes", {}],
  "browser:start_recording": ["start_video_recording", {}],
  "browser:stop_recording": ["stop_video_recording", {}],
};

// Argument names carried over: [action or "*", name] -> ours.
const ARGUMENTS: Record<string, string> = {
  "*:element_index": "element",
  "*:ref": "element",
  "*:index": "element",
  "*:uid": "element",
  "*:element_id": "element",
  "*:to_ref": "to_element",
  "*:target_ref": "to_element",
  "*:to_element_index": "to_element",
  "*:duration": "seconds",
  "*:button": "mouse_button",
  "*:page": "tab",
  "*:tab_id": "tab",
  "click:key": "modifiers",
  "press_key:keys": "key",
  "press_key:text": "key",
  "scroll:amount": "pixels",
  "scroll:scroll_direction": "direction",
  "set_value:values": "value",
  "set_value:option": "value",
  "set_value:label": "value",
  "set_value:text": "value",
  "type_text:value": "text",
  "evaluate:expression": "script",
  "evaluate:function": "script",
  "evaluate:code": "script",
};

/** An action in its present name, with the arguments a carried-over name implies. */
export function presentAction(family: string, action: string): { action: string; implied: Args } {
  const found = ACTIONS[`${family}:${action}`] ?? ACTIONS[`*:${action}`];
  return found ? { action: found[0], implied: { ...found[1] } } : { action, implied: {} };
}

function point(value: unknown): [unknown, unknown] | null {
  if (Array.isArray(value) && value.length === 2) return [value[0], value[1]];
  const item = record(value);
  return "x" in item && "y" in item ? [item.x, item.y] : null;
}

/** One flat step ({action, element, …}) in the present names. */
export function presentStep(family: string, step: Args): Args {
  const { action: written, ...rest } = step;
  const { action, implied } = presentAction(family, typeof written === "string" ? written.trim() : "");
  const renamed: Args = {};
  for (const [key, value] of Object.entries(rest)) {
    const ours = ARGUMENTS[`${action}:${key}`] ?? ARGUMENTS[`*:${key}`];
    const name = ours && (rest[ours] === undefined || rest[ours] === null || rest[ours] === "") ? ours : key;
    if (key === "changes_only" && action === "get_state") renamed.full = !value;
    else renamed[name] = value;
  }
  const start = point(renamed.start_coordinate), at = point(renamed.coordinate);
  if (action === "drag" && start && at && renamed.x === undefined) {
    delete renamed.start_coordinate; delete renamed.coordinate;
    [renamed.x, renamed.y, renamed.to_x, renamed.to_y] = [...start, ...at];
  } else if (at && renamed.x === undefined && renamed.y === undefined) {
    delete renamed.coordinate;
    [renamed.x, renamed.y] = at;
  }
  if (action === "press_key" && Array.isArray(renamed.key)) renamed.key = renamed.key.map(String).join("+");
  return { ...implied, ...renamed, ...(action ? { action } : {}) };
}

/** One step with its arguments beside its action, in the present names. */
function flatStep(family: string, step: unknown): Args {
  const item = record(step);
  const { args, ...rest } = item;
  return presentStep(family, { ...rest, ...record(args) });
}

/** What a call names at its top level, in the names its steps share: the application and window of a
 *  window call, the tab of a browser call, the display of a screen call. */
function shared(args: Args, family: string): Args {
  const out: Args = { ...record(args.args) };
  const title = word(args.title);
  if (family === "window") {
    if (word(args.app)) out.app = word(args.app);
    if (/^\d+$/.test(title)) out.window_id = Number(title);
    else if (title) out.window = title;
  } else if (family === "browser" && title) {
    out.tab = title;
  } else if (family === "screen" && word(args.display)) {
    out.display = word(args.display);
  }
  return out;
}

/** A call as one flat step: {action, app, …} in the present names. Several
 *  steps become a batch, code its own action, and a call with no action
 *  reads the target (or lists the applications of a window call that names
 *  none). */
export function callStep(args: Args): Args {
  const { family } = callTarget(args);
  const common = shared(args, family);
  if (typeof args.code === "string" && args.code.trim()) return { ...common, action: "code", code: args.code };
  if (Array.isArray(args.actions) && args.actions.length) {
    const steps = args.actions.map(step => flatStep(family, step));
    // The application is named once for the call, or by its steps: the worker grants the first one a
    // step names, so the batch names it too and the question asks about it.
    const named = family === "window" && !(typeof common.app === "string" && common.app)
      ? steps.find(step => typeof step.app === "string" && step.app)?.app
      : undefined;
    return { ...common, ...(named ? { app: named } : {}), action: "batch", actions: steps };
  }
  if (typeof args.action === "string" && args.action.trim()) return presentStep(family, { ...common, action: args.action });
  const action = family !== "window" || (typeof common.app === "string" && common.app) ? "get_state" : "list_apps";
  return { ...common, action };
}

/** An application-window call as one flat step, or null for the other types. */
export function appStepArgs(args: Args): Args | null {
  return callTarget(args).family === "window" ? callStep(args) : null;
}

/** A call's action, or how many steps it runs, and the first step's URL: a browser row's line. */
export function screenStep(args: Args): { action: string; steps: number; url: string } {
  const step = callStep(args);
  const steps = Array.isArray(step.actions) ? step.actions.length : 0;
  const first = steps ? record((step.actions as unknown[])[0]) : step;
  const action = step.action === "batch" ? "" : String(step.action ?? "");
  return { action, steps, url: typeof first.url === "string" ? first.url : "" };
}

/** A result's first line names its target when the call landed on another
 *  device ('[device="lab", type="screen"]'), or on this computer while the
 *  conversation's shell runs elsewhere ('[type="window", this computer]'). */
const HEADER = /^\s*\[(?:device="[^"\n]*", )?type="(?:window|screen|browser)"(?:, app="[^"\n]*")?(?:, [^\]\n]*)?\][ \t]*(?:\r?\n|$)/i;
// Tool guides delivered in front of a result are instructions, not the result.
const GUIDES = /^(\s*<guide name="[^"]*">[\s\S]*?<\/guide>)+/;

/** A result without the target header and the guides in front of it, in either order. */
export function resultBody(raw: string): string {
  return raw.replace(HEADER, "").replace(GUIDES, "").replace(HEADER, "").trim();
}

/** Whether a result says the call failed: "Error: …" or a coded failure such as "UNKNOWN_TARGET: …". */
const FAILURE = /^(?:Error|UNKNOWN_TARGET|UNKNOWN_ACTION|NOT_SUPPORTED|BAD_ARGUMENT|PERMISSION_MISSING|DEPENDENCY_MISSING|MACHINE_UNREACHABLE|OUTCOME_UNKNOWN|SCREEN_BUSY|ELEMENT_GONE|TIMEOUT):/;

export function resultFailed(raw: string | null | undefined): boolean {
  return typeof raw === "string" && FAILURE.test(resultBody(raw));
}
