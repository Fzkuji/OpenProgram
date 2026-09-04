# GUI Agent

Give it one natural-language task. Its root controller repeatedly chooses one bounded capability: `computer_use` for the local desktop, `browser_use` for an OpenProgram background Page, or `vm_use` for a configured remote virtual machine. Every capability input and full result is appended to the next model decision's context. The model ends the task by proposing a terminal result; action and time limits remain safety boundaries.

Local and VM perception combines YOLO component detection (GPA-GUI-Detector), OCR (Apple Vision on macOS, EasyOCR on Linux / Windows), and template matching. The action layer covers mouse, keyboard, and clipboard. Browser operations use the Page's DOM/CDP target instead of desktop coordinates.

## Availability

Every supported release registers this Program and ships Playwright Chromium plus the GPA detector weight. It does not ship PyTorch, OpenCV, or EasyOCR, so desktop perception that needs those libraries is unavailable in the packaged product. Source-development checkouts can still install the harness's own dependencies. Developers can use an editable GUI harness checkout or replace the OCR/browser backend for debugging and backend work.

## Usage

The public entry function is **`gui_agent`**, registered as a tool (`as_tool=True`, toolset `harness`). Its public input is only `task`. The planner and the three `*_use` functions remain traceable child function nodes, but they are not registered as separate public tools.

Run it directly from the command line:

```bash
openprogram programs run gui_agent -a task="Open Firefox and go to google.com"
```

The Programs card asks only for `task`. There is no required surface selector. On every iteration the controller reads the original task, the exact prior capability inputs and outputs, and current capability availability before choosing the next function.

For a task that is naturally satisfied by the current built-in browser Page, use the same entry:

```bash
openprogram programs run gui_agent -a task="Inspect and complete the current built-in browser form without foregrounding the window"
```

Trusted callers can also supply hidden controller settings: `max_steps` is the action safety limit (default 150); `max_seconds` is the optional wall-clock safety limit; `app_name` selects component memory; `backend` pins an existing Page backend; and `vm_url` enables `vm_use`. The old `surface` field is accepted only as a compatibility preference. It does not lock the run to one capability and is absent from the public function schema.

The control sequence is:

1. `plan_next_capability` receives the task, current availability, and complete ordered capability history.
2. It selects `computer_use`, `browser_use`, `vm_use`, or proposes a terminal result.
3. `call_capability` binds controller-owned runtime settings and invokes exactly the selected function.
4. The exact function input and full output are appended to history and therefore visible to the next decision.
5. A proposed terminal result is validated. Unsupported success is recorded and planning continues.

`computer_use` and `vm_use` each execute one existing Harness step: observe the current target, verify prior feedback when present, plan one action, execute it, and return the step plus next feedback. `browser_use` executes one bounded background Page sub-task and then returns control to the root loop. There is no special pre-route for screen-reading tasks.

The implementation uses OpenProgram's high-level agentic programming calls. `plan_next_capability`, desktop planning, verification, and conclusion call `llm()` with the active Runtime context. The Browser Page action loop calls `agent()` with its action tool and a single bounded iteration. GUI workflow code does not call `Runtime.exec` directly. The root controller does not wrap itself in a nested `goal()` call because it already owns the capability history, terminal proposal, evidence validation, timeout, cancellation, and no-progress decisions; a second goal controller would duplicate those decisions.

`vm_use` requires an OSWorld-compatible HTTP endpoint. Screenshots are read from `GET /screenshot`, and input commands are sent to `POST /execute`. VM target selection is serialized within the Harness process. Whether the call succeeds or raises, the prior input target and screenshot backend are restored before another capability runs. Endpoint credentials and query values are not included in planner availability context.

Desktop observations include the frontmost application and screenshot coordinate bounds. If the target application's windows are minimized or located in another macOS Space and remain unavailable after one bounded Window-menu recovery, the run stops as infeasible and asks the user to move or unminimize the window. It does not create additional windows indefinitely.

Desktop coordinate input always applies to the current foreground GUI. When the controller has an exact macOS process and window target, `computer_use` may instead use window-only capture and supported Accessibility press, text-value, or scroll actions without activating the target. A non-activating, mouse-ignoring indicator follows that window and marks the current action without moving the system pointer. Browser actions use the selected Page in the background and do not activate its tab, raise the OpenProgram window, or move the system pointer. The controller may switch between these capabilities when the recorded results require it.

All runs share the same terminal fields: `status` (`succeeded`, `infeasible`, or `failed`), `success`, `reason_code`, `summary`, and `handoff_instruction`. The runner, not the conclusion model, determines success. `success` is true only for `succeeded`. Infeasible and failed results always return `success=false`; infeasible results retain the blocker, marker, and user handoff instruction. The result also contains the ordered capability history and timing.

`max_seconds` is enforced before each model or capability call and again after it returns. A terminal proposal that arrives after the deadline is rejected and normalized as a timeout failure. Provider cancellation is cooperative, so an in-flight provider request can return slightly after the configured wall-clock boundary; it still cannot turn that run into success.

The Function card displays that task result directly: `Succeeded` for a verified result, `Failed` when the task ended without satisfying the request, and `Needs takeover` when the handoff instruction requires user action. `Error` identifies a runtime exception or an invalid GUI result contract. An internal completed worker state never changes a failed GUI result into `Completed`.

## Dependency notes

- Product runtimes do not install PyTorch or EasyOCR.
- The release capability probe rejects an artifact if the detector model is missing.
- Supported product platforms are macOS and Linux.
- The runtime needs a working directory configured before running. Workflow records are stored under the OpenProgram state directory (`gui_harness/workflows/`), not in the source tree.

Source and README: `openprogram/programs/applications/gui_harness/`, upstream repository [Fzkuji/GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness).
