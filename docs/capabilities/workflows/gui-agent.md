# GUI Agent

Give it a natural-language task and it operates the desktop autonomously: taking screenshots, detecting UI components, clicking, typing, and verifying results, looping until the task completes or the step limit is reached. It works on the local desktop and can also drive remote virtual machines through a VM interface. The perception layer combines YOLO component detection (GPA-GUI-Detector), OCR (Apple Vision on macOS, EasyOCR on Linux / Windows), and template matching; the action layer covers mouse, keyboard, and clipboard. On the OSWorld benchmark it scores 79.8% on the Multi-Apps split ([results](https://github.com/Fzkuji/GUI-Agent-Harness/blob/main/benchmarks/osworld/multi_apps.md)).

## Install

```bash
openprogram programs install gui
```

This is the heaviest of the three first-party workflows: dependencies include `torch` / `torchvision`, `opencv-python`, `ultralytics`, `easyocr`, `pynput`, and more. On Linux without a GPU it automatically picks the CPU torch wheel (about 300 MB) instead of the roughly 3 GB CUDA build.

Beyond the pip dependencies you also need model files — the YOLO detector weights (downloaded via `huggingface-hub`) and the OCR models are not on PyPI. After installing, run the harness's bundled installer to fetch them:

```bash
openprogram/functions/agentics/GUI-Agent-Harness/scripts/install.sh --no-host
# Windows: ...\scripts\install.ps1 -NoHost
```

## Usage

The entry function is **`gui_agent`**, registered as a tool (`as_tool=True`, toolset `harness`). In chat, just describe a desktop task to trigger it, e.g. "Open Firefox and go to google.com".

Run it directly from the command line:

```bash
openprogram programs run gui_agent -a task="Open Firefox and go to google.com"
```

Parameters (function signature `gui_agent(task, max_steps=15, app_name="desktop", ...)`):

| Parameter | Description |
|---|---|
| `task` | What to do, in natural language |
| `max_steps` | Maximum number of actions before giving up; default 15, choices 5 / 10 / 15 / 30 |
| `app_name` | Application name used for component memory, e.g. `firefox`, `libreoffice_calc`; default `desktop` |

Each step runs observe (screenshot + component detection + state recognition) → verify the previous step's result → plan the next action → execute → build feedback for the next round. Structured feedback is passed between steps, so progress does not depend on the LLM's context memory. Previously learned UI transitions are reused as shortcuts (component memory).

## Dependency notes

- PyTorch + OpenCV are large; installation takes minutes. No transformers / accelerate needed.
- The model weights are a separate download step (see above); without them the function registers but cannot perceive the screen.
- Platforms: macOS / Windows / Linux; the OCR backend is selected automatically per platform.
- The runtime needs a working directory configured before running (screenshots and run records are written there).

Source and README: `openprogram/functions/agentics/GUI-Agent-Harness/`, upstream repository [Fzkuji/GUI-Agent-Harness](https://github.com/Fzkuji/GUI-Agent-Harness).
