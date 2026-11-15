/** What a computer_use approval says about its one step, from the call's arguments. */
export type Args = Record<string, unknown>;
export type Text = (en: string, zh: string) => string;

export const AUDIO_ACTIONS = new Set(["start_audio_recording", "stop_audio_recording"]);

export function str(value: unknown): string {
  return typeof value === "string" ? value.trim() : typeof value === "number" ? String(value) : "";
}

function where(args: Args, text: Text): string {
  const index = args.element_index;
  if (typeof index === "number" || (typeof index === "string" && index)) return text(`element #${index}`, `元素 #${index}`);
  const x = str(args.x), y = str(args.y);
  return x && y ? `(${x}, ${y})` : "";
}

/** What this one step does, as a verb and its object: "Click element #7", "Type 12 characters". */
export function computerUseStep(args: Args, text: Text): { verb: string; target: string } {
  const action = str(args.action);
  const content = str(args.text);
  const chars = Array.from(content).length;
  switch (action) {
    case "get_app_state": return { verb: text("Read the window", "读取窗口内容"), target: "" };
    case "list_windows": return { verb: text("List windows", "列出窗口"), target: "" };
    case "zoom": return { verb: text("Zoom into a region", "放大查看局部"), target: "" };
    case "find": return { verb: text("Find", "查找"), target: content };
    case "click": return {
      verb: Number(args.click_count) === 2 ? text("Double-click", "双击")
        : str(args.mouse_button) === "right" ? text("Right-click", "右键点击") : text("Click", "点击"),
      target: where(args, text),
    };
    case "hover": return { verb: text("Hover over", "悬停在"), target: where(args, text) };
    case "type_text": return { verb: text("Type", "输入"), target: text(`${chars} characters`, `${chars} 个字`) };
    case "paste": return { verb: text("Paste", "粘贴"), target: text(`${chars} characters`, `${chars} 个字`) };
    case "set_value": return { verb: text("Set the value of", "设置值："), target: where(args, text) };
    case "select_text": return { verb: text("Select text", "选中文字"), target: content };
    case "press_key": return { verb: text("Press", "按"), target: str(args.key) };
    case "scroll": return { verb: text("Scroll", "滚动"), target: str(args.direction) || text("down", "向下") };
    case "drag": return { verb: text("Drag", "拖拽"), target: where(args, text) };
    case "perform_secondary_action": return { verb: text("Perform", "执行"), target: str(args.action_name) };
    case "copy": return { verb: text("Copy the selection", "复制选中内容"), target: "" };
    case "focus": return { verb: text("Focus", "聚焦"), target: where(args, text) };
    case "show_window": return { verb: text("Show the window", "显示窗口"), target: str(args.window) };
    case "menu": return {
      verb: text("Choose the menu item", "选择菜单"),
      target: Array.isArray(args.menu_path) ? args.menu_path.map(str).filter(Boolean).join(" › ") : "",
    };
    case "batch": {
      const steps = Array.isArray(args.actions) ? args.actions.length : 0;
      return { verb: text("Run steps in a row", "连续执行"), target: text(`${steps} steps`, `${steps} 步`) };
    }
    case "code": return { verb: text("Run code that operates it", "运行操作它的代码"),
      target: text(`${Array.from(str(args.code)).length} characters`, `${Array.from(str(args.code)).length} 个字符`) };
    case "fresh": return { verb: text("Hand the task to a new agent", "把任务交给新代理"), target: "" };
    case "wait": return { verb: text("Wait", "等待"), target: text(`${str(args.duration) || "1"} s`, `${str(args.duration) || "1"} 秒`) };
    case "start_audio_recording": return { verb: text("Record its sound", "录下它的声音"), target: "" };
    case "stop_audio_recording": return { verb: text("Stop recording", "停止录音"), target: "" };
    default: return { verb: action || text("Operate", "操作"), target: "" };
  }
}

const POINTER_ACTIONS = new Set(["click", "drag", "scroll", "hover"]);
const BACKGROUND_KEYS = new Set(["return", "enter", "kp_enter"]);

/** Whether a step brings the window forward for a moment. On Linux, input at a
 *  coordinate and key presses reach a window only while it is focused. On
 *  Windows, plain input is posted to the window, but a key with modifiers needs
 *  the real keyboard state, and paste presses ctrl+v in most apps. */
export function needsForeground(args: Args, platform = "linux"): boolean {
  const action = str(args.action);
  if (action === "batch") return Array.isArray(args.actions) && args.actions.some((step) =>
    !!step && typeof step === "object" && needsForeground(step as Args, platform));
  if (platform === "windows") {
    if (action === "paste") return true;
    const key = str(args.key);
    return action === "press_key" && key !== "+" && key.replace(/\+\+$/, "+x").split("+").length > 1;
  }
  if (platform !== "linux") return false;
  if (POINTER_ACTIONS.has(action)) {
    const index = args.element_index;
    const byElement = typeof index === "number" || (typeof index === "string" && index !== "");
    // An element click or scroll runs through AT-SPI; a hover always moves the pointer.
    return action === "hover" || action === "drag" || !byElement;
  }
  if (action === "press_key") return !BACKGROUND_KEYS.has(str(args.key).toLowerCase());
  return false;
}

/** What a read returns, shown beside the step. */
export function readsWhat(args: Args, text: Text): string {
  if (str(args.action) !== "get_app_state") return "";
  const include = str(args.include) || "all";
  return include === "tree" ? text("Interface elements", "界面元素")
    : include === "screenshot" ? text("Screenshot", "截图") : text("Screenshot + interface elements", "截图 + 界面元素");
}

/** The arguments without empty values, so the details show what the step uses. */
export function setArgs(args: Args): Args {
  return Object.fromEntries(Object.entries(args).filter(([, value]) =>
    value !== null && value !== undefined && value !== "" && !(Array.isArray(value) && value.length === 0)));
}
