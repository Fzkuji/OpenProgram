# GUI Agent

Give it a natural-language task and it operates the desktop autonomously: taking screenshots, detecting UI components, clicking, typing, and verifying results, looping until the task completes or the step limit is reached. It works on the local desktop and can also drive remote virtual machines through a VM interface. The perception layer combines YOLO component detection (GPA-GUI-Detector), OCR (Apple Vision on macOS, EasyOCR on Linux / Windows), and template matching; the action layer covers mouse, keyboard, and clipboard. On the OSWorld benchmark it scores 79.8% on the Multi-Apps split ([results](https://github.com/Fzkuji/GUI-Agent-Harness/blob/main/benchmarks/osworld/multi_apps.md)).

## Availability

Every supported release registers this Program and ships Playwright Chromium plus the GPA detector weight. It does not ship PyTorch, OpenCV, or EasyOCR, so desktop perception that needs those libraries is unavailable in the packaged product. Source-development checkouts can still install the harness's own dependencies. Developers can use an editable GUI harness checkout or replace the OCR/browser backend for debugging and backend work.

## Usage

The entry function is **`gui_agent`**, registered as a tool (`as_tool=True`, toolset `harness`). In chat, just describe a desktop task to trigger it, e.g. "Open Firefox and go to google.com".

Run it directly from the command line:

```bash
openprogram programs run gui_agent -a task="Open Firefox and go to google.com"
```

The Programs card asks for `task` and a `surface`. Keep `desktop` for foreground OS input, or select `browser` to operate an exact Page in OpenProgram's built-in browser. Browser actions use the Page's DOM/CDP target and do not activate its tab, raise the OpenProgram window, or move the system pointer. Ref clicks are dispatched inside the Page DOM, and screenshots use Electron's hidden-page capture, so neither operation needs to reveal the Page. GUI Agent reuses an existing Page from the OpenProgram window that submitted the message or Function form. If that window has no Page, it creates one background Page at `https://www.google.com/`, continues the same function run, and closes only that agent-created Page when the run ends. It leaves the selected tab unchanged. If no originating desktop window is available, the result is `infeasible` with a handoff instruction instead of a runtime error. If the desktop rejects cleanup, the result is also `infeasible` and tells the user to close the remaining background Page.

Typing a complete registered expression such as `gui_agent(task="Inspect this Page", surface="browser")` uses the same Function dispatcher as the Programs form; it is not stored or executed as an ordinary chat message. Retry reruns that exact Function node and its stored originating window/Page identity instead of using whichever Page is selected when Retry is clicked.

Run against the built-in browser Page:

```bash
openprogram programs run gui_agent -a task="Inspect and complete the visible form" -a surface=browser
```

Parameters (function signature `gui_agent(task, max_steps=None, app_name="desktop", surface="desktop", ...)`):

| Parameter | Description |
|---|---|
| `task` | What to do, in natural language |
| `max_steps` | Maximum number of actions. Default 150. `0` or a negative value means no cap. |
| `max_seconds` | Whole-process deadline for runs dispatched through OpenProgram Programs or Functions, on either surface. Default 300 seconds; a positive value overrides it, while `0` or a negative value removes the deadline. In a direct Python bridge call, the browser loop also enforces this value internally; the desktop harness has no separate internal timer. |
| `app_name` | Application name used for component memory, e.g. `firefox`, `libreoffice_calc`; default `desktop` |
| `surface` | `desktop` for foreground OS/VM input or `browser` for an exact built-in Page. The browser route uses the default Page backend unless a trusted caller supplies `backend`. |

Each desktop step runs observe (one screenshot + component detection + state recognition) → verify the previous step's result → plan one action → execute → build feedback for the next round. A first-step `done` decision receives a separate current-screen completion check. Feedback keeps the latest eight action outcomes; selecting the same failed action four times stops the run instead of repeating indefinitely. Previously learned UI transitions remain available, but learning is an explicit operation rather than an implicit pre-step side effect. When the agent cannot finish or a human must take over, it chooses fail, stops, and returns `success=false`; both `summary` and `handoff_instruction` preserve the handoff text.

Desktop observations include the frontmost application and screenshot coordinate bounds. If the target application's windows are minimized or located in another macOS Space and remain unavailable after one bounded Window-menu recovery, the run stops as infeasible and asks the user to move or unminimize the window. It does not create additional windows indefinitely.

Desktop coordinate input always applies to the current foreground GUI; it is not a background-window API. Use `surface=browser` when a built-in Page must be inspected or changed without bringing OpenProgram or that Page to the foreground. In a multi-window App session, the Page is created in the window that submitted the chat message or Function call. Normal completion, failure, cancellation, timeout, and forced subprocess termination all attempt exact cleanup of an automatically created Page. A rejected cleanup is never reported as success: OpenProgram preserves the Page identity for a bounded retry and returns a manual-close handoff if cleanup still cannot be confirmed.

Desktop and built-in-browser runs share the same terminal fields: `status` (`succeeded`, `infeasible`, `failed`, or `cancelled`), `success`, `reason_code`, `summary`, and `handoff_instruction`. The runner, not the conclusion model, determines success. Desktop results additionally include step history and timing; browser results include backend and WebSession details.

The Function card displays that task result directly: `Succeeded` for a verified result, `Failed` when the task ended without satisfying the request, and `Needs takeover` when the handoff instruction requires user action. `Error` identifies a runtime exception or an invalid GUI result contract. An internal completed worker state never changes a failed GUI result into `Completed`.

## Dependency notes

- Product runtimes do not install PyTorch or EasyOCR.
- The release capability probe rejects an artifact if the detector model is missing.
- Supported product platforms are macOS and Linux.
- The runtime needs a working directory configured before running. Workflow records are stored under the OpenProgram state directory (`gui_harness/workflows/`), not in the source tree.

Source and README: `openprogram/programs/applications/gui_harness/`, upstream repository [Fzkuji/GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness).
