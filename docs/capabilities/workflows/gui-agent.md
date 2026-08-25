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

The Programs card only asks for `task`. Other parameters keep their defaults; CLI and agent calls can still pass them.

Parameters (function signature `gui_agent(task, max_steps=None, app_name="desktop", ...)`):

| Parameter | Description |
|---|---|
| `task` | What to do, in natural language |
| `max_steps` | Maximum number of actions. Default 150. `0` or a negative value means no cap. |
| `max_seconds` | Web-path wall-clock limit only. Default is no time limit. `0` or a negative value means no cap. Desktop does not use this field. |
| `app_name` | Application name used for component memory, e.g. `firefox`, `libreoffice_calc`; default `desktop` |

Each step runs observe (screenshot + component detection + state recognition) → verify the previous step's result → plan the next action → execute → build feedback for the next round. Structured feedback is passed between steps, so progress does not depend on the LLM's context memory. Previously learned UI transitions are reused as shortcuts (component memory).

## Dependency notes

- Product runtimes do not install PyTorch or EasyOCR.
- The release capability probe rejects an artifact if the detector model is missing.
- Supported product platforms are macOS and Linux.
- The runtime needs a working directory configured before running (screenshots and run records are written there).

Source and README: `openprogram/programs/applications/gui_harness/`, upstream repository [Fzkuji/GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness).
