# GUI Agent

Give it one natural-language task. Without an explicit browser selection, its root controller repeatedly chooses one bounded capability: `computer_use` for the local desktop, `browser_use` for an OpenProgram background Page, or `vm_use` for a configured remote virtual machine. Every capability's planner-selected arguments and full result are appended to the next model decision's context. The model ends the task by proposing a terminal result; action and time limits remain safety boundaries.

Local and VM perception combines YOLO component detection (GPA-GUI-Detector), OCR (Apple Vision on macOS, EasyOCR on Linux / Windows), and template matching. The action layer covers mouse, keyboard, and clipboard. Browser operations use the Page's DOM/CDP target instead of desktop coordinates.

## Availability

Every supported release registers this Program and ships Playwright Chromium plus the GPA detector weight. It does not ship PyTorch, OpenCV, or EasyOCR, so desktop perception that needs those libraries is unavailable in the packaged product. Source-development checkouts can still install the harness's own dependencies. Developers can use an editable GUI harness checkout or replace the OCR/browser backend for debugging and backend work.

## Usage

The public entry function is **`gui_agent`**, registered as a tool (`as_tool=True`, toolset `harness`). Its public input is only `task`. The planner and the three `*_use` functions remain traceable child function nodes, but they are not registered as separate public tools.

Run it directly from the command line:

```bash
openprogram programs run gui_agent -a task="Open Firefox and go to google.com"
```

The Programs card asks only for `task`. There is no required surface selector. On every iteration the controller reads the original task, the prior planner-selected capability arguments and full outputs, and current capability availability before choosing the next function.

For a task that is naturally satisfied by the current built-in browser Page, use the same entry:

```bash
openprogram programs run gui_agent -a task="Inspect and complete the current built-in browser form without foregrounding the window"
```

Trusted callers can also supply hidden controller settings: `max_steps` is the action safety limit (default 150); `max_seconds` is the optional wall-clock safety limit; `app_name` selects component memory; `backend` pins an existing Page backend; and `vm_url` enables `vm_use`. `surface="browser"`, or a `backend` without another surface, selects the standard browser Agent path described below. Other surface settings remain compatibility preferences. These settings are absent from the public function schema.

## Explicit browser execution

Inside an active macOS Runtime execution, trusted callers can select `surface="browser"`. This path uses one standard `agent()` loop with the persistent, isolated `gui_exec` Python tool. It lists context-authorized Pages, acquires single-use Page capabilities, and exposes opaque handles. It does not open a new Page automatically or run the legacy capability planner. The default backend is `open_claude_chrome`; explicit MCP backends without operation guards return `infeasible` rather than silently selecting another backend.

`max_steps` limits Agent iterations on this path. Each Python call is separately bounded to 30 seconds and 100 broker operations. `allow_general=false` exposes only the GUI tool; `true` also makes standard tools available subject to inherited permissions and deny rules, excluding recursive `gui_agent`. Python state persists for this invocation; screenshot bytes are returned as normal Agent image content. Missing execution identity or unsupported process isolation fails closed.

The model proposes a final browser assertion. The host checks it against the owned Page and current frame after the Agent returns. Success requires that assertion to pass and no unresolved primitive effects from this invocation. The result includes the verification effect and assertion evidence; a model-selected assertion does not prove every aspect of an arbitrary task. Script completion alone cannot report success. Handles are revoked before Page leases are released, and cleanup errors prevent a successful result. Cleanup may still wait for an already-running browser operation; bounded in-flight cancellation and default-App acceptance remain unverified.

## Browser resources and human control

In the desktop App, Agent-opened Pages remain in the background. Open **Resources** in the right sidebar to see the current conversation's Pages grouped by branch, including Pages used by its child agents. Continuing a branch keeps its resource group. Distinct Pages stay distinct even when they have the same URL; pages without established branch ownership appear under **Unassigned**.

Select a resource to inspect its image preview in chat. **Preview in conversation** is the explicit control for the same existing Page: it does not add a top tab, enters manual inspection, and if a webpage currently occupies center, returns to the owning session so the chat preview is visible. **Follow** selects the Page most recently operated on by that branch; selecting a resource manually keeps that selection while the Agent works elsewhere and does not steal tab focus. **Hide** stops preview capture and keeps the Page available. **Expand** enlarges the image preview. **Open page** opens the existing Page as an ordinary top tab and hides the chat preview. Returning to the conversation restores the preview unless it was hidden. These controls keep their selection per conversation and branch.

A webpage tab and its preview never display together. The actual webpage remains usable in its own tab. There is no dimming mask. Its toolbar identifies the current controller and provides **Take over**, **Resume**, **Show actions**, and **Operation history**. The chat preview uses a single compact chrome row; Idle has no enabled Take over. Action markers are brief and do not intercept input; history contains operation types and results rather than the text entered into a webpage.

Using the real webpage requests yielding: clicking, typing, scrolling, and navigating close later Agent input admission and request execution pause. Merely focusing the page or inspecting its preview does not pause execution. An action already dispatched can still finish. **Yielding** means stopping is pending; **Paused** requires execution acknowledgement and reconciliation. **Stop unconfirmed** means the App has not confirmed stopping, and the webpage remains usable. Resuming is explicit and requires a fresh observation and current permission.

Closing a Page affects every branch reference to that same Page. An active Page first requests stopping; it stays available until stopping is confirmed. Closing is separate from hiding the preview. Disconnection or an unavailable target shows the last image as stale. Saved resource metadata does not restore an Agent input lease or recreate a closed Page.

These browser controls operate on exact OpenProgram Pages. Native application windows, the shared host desktop, and VM displays retain their own capability and input-scope restrictions described below.

## Automatic capability execution

The following sequence applies when no explicit browser path is selected:

1. `plan_next_capability` receives the task, current availability, and complete ordered capability history.
2. It selects `computer_use`, `browser_use`, `vm_use`, or proposes a terminal result.
3. `call_capability` binds controller-owned runtime settings and invokes exactly the selected function.
4. The planner-selected arguments and full function output are appended to history and therefore visible to the next decision. Controller-bound feedback is recovered from the previous output's `next_feedback`; it is not duplicated inside the next history input.
5. A proposed terminal result is validated. Unsupported success is recorded and planning continues.

`computer_use` and `vm_use` each execute one existing Harness step: observe the current target, verify prior feedback when present, plan one action, execute it, and return the step plus next feedback. `browser_use` executes one bounded background Page sub-task and then returns control to the root loop. There is no special pre-route for screen-reading tasks.

The implementation uses OpenProgram's high-level agentic programming calls. `plan_next_capability`, desktop planning, verification, and conclusion call `llm()` with the active Runtime context. The Browser Page action loop calls `agent()` with its action tool and a single bounded iteration. GUI workflow code does not call `Runtime.exec` directly. The root controller does not wrap itself in a nested `goal()` call because it already owns the capability history, terminal proposal, evidence validation, timeout, cancellation, and no-progress decisions; a second goal controller would duplicate those decisions.

`vm_use` requires an OSWorld-compatible HTTP endpoint. Screenshots are read from `GET /screenshot`, and input commands are sent to `POST /execute`. VM target selection is serialized within the Harness process. Whether the call succeeds or raises, the prior input target and screenshot backend are restored before another capability runs. Endpoint credentials and query values are not included in planner availability context.

Desktop observations include the frontmost application and screenshot coordinate bounds. If the target application's windows are minimized or located in another macOS Space and remain unavailable after one bounded Window-menu recovery, the run stops as infeasible and asks the user to move or unminimize the window. It does not create additional windows indefinitely.

Desktop coordinate input always applies to the current foreground GUI. When the controller has an exact macOS process and window target, `computer_use` may instead use window-only capture and supported Accessibility press, text-value, or scroll actions without activating the target. A non-activating, mouse-ignoring indicator follows that window and marks the current action without moving the system pointer. Browser actions use the selected Page in the background and do not activate its tab, raise the OpenProgram window, or move the system pointer. The controller may switch between these capabilities when the recorded results require it.

All runs share the same terminal fields: `status` (`succeeded`, `infeasible`, or `failed`), `success`, `reason_code`, `summary`, and `handoff_instruction`. The runner, not the conclusion model, determines success. `success` is true only for `succeeded`. Infeasible and failed results always return `success=false`; infeasible results retain the blocker, marker, and user handoff instruction. The automatic capability path also contains its ordered capability history and timing.

`max_seconds` is enforced before each model or capability call and again after it returns. A terminal proposal that arrives after the deadline is rejected and normalized as a timeout failure. Provider cancellation is cooperative, so an in-flight provider request can return slightly after the configured wall-clock boundary; it still cannot turn that run into success.

The Function card displays that task result directly: `Succeeded` for a verified result, `Failed` when the task ended without satisfying the request, and `Needs takeover` when the handoff instruction requires user action. `Error` identifies a runtime exception or an invalid GUI result contract. An internal completed worker state never changes a failed GUI result into `Completed`.

## Dependency notes

- Product runtimes do not install PyTorch or EasyOCR.
- The release capability probe rejects an artifact if the detector model is missing.
- Program registration is included on macOS, Linux, and Windows x86_64
  runtimes; individual desktop backends still follow the harness's platform
  and dependency support.
- The runtime needs a working directory configured before running. Workflow records are stored under the OpenProgram state directory (`gui_harness/workflows/`), not in the source tree.

Source and README: `openprogram/programs/applications/gui_harness/`, upstream repository [Fzkuji/GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness).

Browser Workflow forms expose only the task and optional target URL. Action limits, timeout and backend remain internal settings with defaults; no Advanced section is shown. Explicit programmatic calls retain their supported overrides.

## System access diagnostics

System settings shows live optional desktop access on the execution computer.
Opening this page or running `openprogram doctor` does not request permission.
The TUI `/doctor` command and `GET /api/system/access` expose the same checks.
Missing optional desktop access does not prevent ordinary chat or upgrades.

On macOS, managed installations run the worker and its Python children through
**OpenProgram**, an embedded application with the OpenProgram icon and
a stable bundle identifier. This is the name to look for in System Settings;
the installer does not grant its permissions. Screen recording and Accessibility
are checked separately for the executing process. In local System settings, **Set up access** explicitly requests
only a missing permission; an existing grant is left untouched. Complete the
system confirmation, then return to the page for automatic verification.
Remote clients must arrange authorization on the execution computer. A grant
for another application is not evidence that the worker is authorized.

Linux headless sessions and unsupported Wayland desktop capture are reported
separately from missing dependencies. An X11 display alone does not prove access.
Windows desktop access depends on the active session and target; ordinary
applications cannot assume access to secure desktops or elevated targets.
Neither platform is instructed to disable security or run the entire application
as administrator. Native Linux and Windows desktop acceptance remains unverified.

The current macOS development build is not production-signed. Permission
persistence across signed release upgrades, first-run capability onboarding,
and automatic task recovery after authorization remain unverified. System
access setup never retries a declined operation.


### System authorization

Local desktop tasks request missing system access through the native authorization flow. The task row only shows a brief status and an Open System Settings text action, without a separate permission card, form, or Continue button. The active waiting task rechecks access and continues once after authorization takes effect. Viewing history never executes a task automatically. Navigating away stops automatic continuation. System access does not replace operation approval.
