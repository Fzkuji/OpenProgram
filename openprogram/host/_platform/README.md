# `openprogram/host/_platform/`

> Platform operations that differ between POSIX and Windows, one module per area.

## Overview

Callers import the module that owns the operation: ``filesystem`` (paths,
owner-only permissions, directory handles), ``locking`` (``flock``),
``processes`` (liveness, kill trees, port owners), ``process_exits``
(learning the moment a process ends), ``process_ownership``
(process groups and Windows jobs), ``command_shell`` (which shell a
command runs in), ``terminal`` (shell invocations, PTYs, bundled tools),
``environment`` (browser, WSL, release and service backends),
``screen_capture_audio`` (audio-only ScreenCaptureKit streams on macOS),
``system_sound`` (the computer's sound cut into chunks for an application's recording),
``architecture`` (the CPU architecture the interpreter runs as, which
picks packages), ``front`` (giving the front back to the application the user is in),
``power`` (holding the system awake during an execution), ``clipboard``
(copying text) and ``asyncio``.

## Files in this directory

- **`architecture.py`** — The CPU architecture this Python runs as, in the release matrix's words
- **`asyncio.py`** — Platform asyncio
- **`changes.py`** — How a Worker learns that something changed, instead of looking again
- **`clipboard.py`** — Putting text on the system clipboard with the tool each platform ships
- **`command_shell.py`** — Which shell a command runs in, decided in one place
- **`environment.py`** — Platform environment
- **`file_manager.py`** — Showing a file in the file manager of the computer the worker runs on
- **`filesystem.py`** — Platform filesystem
- **`front.py`** — What the user has in front stays in front while an agent operates something else
- **`host_facts.py`** — What the layers above tell the platform layer
- **`locking.py`** — Advisory file locking with the POSIX ``fcntl.flock`` surface on every platform
- **`power.py`** — Optional idle-system-sleep assertion for active agent execution
- **`process_exits.py`** — Learning that a process ended the moment it ends
- **`process_ownership.py`** — Platform process ownership
- **`processes.py`** — Platform processes
- **`runtime_layout.py`** — Where everything lives under ``<state>/runtime``, and whether we run from it
- **`screen_capture_audio.py`** — Audio-only ScreenCaptureKit streams on macOS, and the WAV their samples become
- **`screen_windows.py`** — The windows on screen, front to back, with their frames: what a screenshot snaps to
- **`system_sound.py`** — The computer's sound, recorded in the worker and cut into chunks
- **`terminal.py`** — Platform terminal
- **`windows_ocr.py`** — Text in an image, read by the OCR engine Windows ships (``Windows.Media.Ocr``)

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
