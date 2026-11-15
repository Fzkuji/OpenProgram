"""Platform operations that differ between POSIX and Windows, one module per area.

Callers import the module that owns the operation: ``filesystem`` (paths,
owner-only permissions, directory handles), ``locking`` (``flock``),
``processes`` (liveness, kill trees, port owners), ``process_ownership``
(process groups and Windows jobs), ``command_shell`` (which shell a
command runs in), ``terminal`` (shell invocations, PTYs, bundled tools),
``environment`` (browser, WSL, release and service backends),
``screen_capture_audio`` (audio-only ScreenCaptureKit streams on macOS),
``architecture`` (the CPU architecture the interpreter runs as, which
picks packages), ``front`` (giving the front back to the application the user is in),
``power`` (holding the system awake during an execution), ``clipboard``
(copying text) and ``asyncio``.
"""
