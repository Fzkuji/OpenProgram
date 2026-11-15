"""Container commands and usage parsing for the Harbor adapter.

Everything here is plain Python with no Harbor import, so it can be tested
without Harbor installed. ``agent.py`` feeds these strings to Harbor's
``exec_as_root`` / ``exec_as_agent`` and turns the usage summary into
``AgentContext`` fields.

The container side has these steps:

1. install: uv (pinned) puts a pinned Python and the ``openprogram`` CLI
   under ``/installed-agent/openprogram``, apart from the task's own
   Python, so any image works, with or without Python 3.11+. Every
   dependency is held to the versions of the commit's ``uv.lock`` (a
   constraints file exported from it), so every trial runs the same code;
2. run: ``openprogram --print=- --standalone --no-project-binding
   --output-format stream-json`` runs the instruction, read from stdin, in
   the task's working directory, without a terminal; the provider key is
   the provider's standard environment variable of that one process (never
   stored, never on a command line), nothing is written into the graded
   folder, stdout (the events) and stderr are kept apart, and it writes
   ``--usage-file`` and ``--transcript-file`` (the turn's requests,
   replies, tool calls and results, in order). Should Harbor's agent
   timeout end the run first, :func:`stop_command` sends OpenProgram
   SIGTERM, which cancels the turn and still writes the usage summary;
3. finish, whatever happened before: :func:`finish_command` copies
   OpenProgram's state (the session log with the turn's journal, the usage
   ledger) into the logs, without any credential, and deletes any
   credential left there;
4. rebuild: when the run was killed before it wrote its transcript,
   :func:`rebuild_transcript_command` writes it from that state with
   ``openprogram transcript``, the same code ``--transcript-file`` runs.

:func:`run_attempt` puts the steps of one attempt in that order, and
:func:`harbor_matcher_applies` says which commands Harbor's error matcher
may type; ``agent.py`` hands both decisions here.
"""
from __future__ import annotations

import asyncio
import json
import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable

#: uv release used to install Python and OpenProgram in the task container.
UV_VERSION = "0.11.16"
#: Interpreter OpenProgram runs on, downloaded by uv when the image has none.
PYTHON_VERSION = "3.12"

INSTALL_ROOT = PurePosixPath("/installed-agent/openprogram")
OPENPROGRAM = INSTALL_ROOT / "bin" / "openprogram"
#: Where an uploaded wheel and the constraints file are placed before
#: installation.
WHEEL_DIR = INSTALL_ROOT / "wheels"
CONSTRAINTS_PATH = WHEEL_DIR / "constraints.txt"
#: The resolved package versions, written by the install step and copied
#: into each trial's logs.
PACKAGES_FILE = INSTALL_ROOT / "packages.txt"
#: Where a prebuilt install (see :func:`bundle_install_command`) is uploaded.
BUNDLE_PATH = PurePosixPath("/installed-agent/openprogram-bundle.tar.gz")
#: Shell test (run as root) that a prebuilt ``x86_64`` glibc install can run
#: in the container: the bundle's Python and wheels need glibc, and ``tar``
#: and ``gzip`` unpack it.
BUNDLE_USABLE_CHECK = (
    '[ "$(uname -m)" = x86_64 ] && getconf GNU_LIBC_VERSION >/dev/null 2>&1 '
    "&& command -v tar >/dev/null 2>&1 && command -v gzip >/dev/null 2>&1"
)

#: Harbor syncs this directory back to the trial's ``agent/`` folder.
LOGS_DIR = PurePosixPath("/logs/agent")
USAGE_FILE_NAME = "openprogram-usage.json"
TRANSCRIPT_FILE_NAME = "openprogram-transcript.jsonl"
#: Harbor's trajectory file (ATIF), read by ``harbor view``.
TRAJECTORY_FILE_NAME = "trajectory.json"
OUTPUT_FILE_NAME = "openprogram.txt"
PACKAGES_FILE_NAME = "openprogram-packages.txt"
#: What the run wrote on stderr, kept apart from the stdout events.
STDERR_FILE_NAME = "openprogram.stderr.txt"
#: The running OpenProgram's process id, for :func:`stop_command`.
PID_FILE_NAME = "openprogram.pid"
#: OpenProgram's state, copied out of the agent's home by
#: :func:`finish_command`; a named profile's state goes to
#: ``openprogram-state-<profile>``.
EVIDENCE_DIR_NAME = "openprogram-state"
#: OpenProgram's state directories in the agent user's home, as the shell
#: names them: ``~/.openprogram`` and, under ``OPENPROGRAM_PROFILE``,
#: ``~/.openprogram-<profile>``. The finish takes every one there is, so it
#: never depends on seeing the variable the run saw.
STATE_DIRS = '"$HOME"/.openprogram "$HOME"/.openprogram-*'
#: Entries of the state directory that never leave the container: the
#: stored credential, and the provider catalog cache (no evidence).
STATE_NOT_COPIED = ("auth", "cache")
#: The flag only the run command carries; Harbor's error matcher is kept off
#: that command's output (see ``OpenProgramAgent._classify_exec_error``).
PRINT_FLAG = "--print="

#: Seconds :func:`stop_command` waits for OpenProgram to cancel its turn and
#: write the usage summary after SIGTERM; OpenProgram itself gives the turn
#: 30 s to stop.
STOP_GRACE_SEC = 40

#: Environment variable that carries the provider key into the run command,
#: which hands it to OpenProgram under the provider's own name
#: (:func:`key_env_name`) and unsets it.
API_KEY_ENV = "OPENPROGRAM_HARBOR_API_KEY"
#: Environment variable that carries the instruction to the run command,
#: which pipes it to ``--print=-``: no instruction text in a command line.
INSTRUCTION_ENV = "OPENPROGRAM_HARBOR_INSTRUCTION"

DEFAULT_GIT_URL = "https://github.com/FuzzyKuji/OpenProgram"

#: The variable OpenProgram reads a provider's key from, where it is not
#: ``<PROVIDER>_API_KEY`` (``openprogram/models/providers/settings/
#: env_api_keys.py``); ``key_env`` in the agent kwargs names any other.
_KEY_ENV = {
    "google": "GEMINI_API_KEY", "gemini-subscription": "GEMINI_API_KEY",
    "vercel-ai-gateway": "AI_GATEWAY_API_KEY", "huggingface": "HF_TOKEN",
    "kimi-coding": "KIMI_API_KEY", "kimi_coding": "KIMI_API_KEY",
    "opencode-go": "OPENCODE_API_KEY",
}

ENDED_COMPLETED = "completed"


class AdapterConfigError(ValueError):
    """The adapter was configured in a way that cannot run."""


def package_spec(*, wheel_path: str | None, git_ref: Any,
                 git_url: str = DEFAULT_GIT_URL) -> str:
    """What ``uv tool install`` installs: a wheel already in the container,
    or a pinned Git ref. Exactly one of the two must be given; with the
    constraints file (see :func:`install_command`) every trial of a run
    installs the same code.

    Harbor reads every ``--ak`` value as JSON when it parses, so a commit id
    of digits and one ``e`` arrives as a number (``1234e56`` is
    ``1.234e+59``). Only a string is taken as a ref.
    """
    if git_ref is not None and not isinstance(git_ref, str):
        raise AdapterConfigError(
            f"git_ref must be a string, got {type(git_ref).__name__} {git_ref!r}: "
            "Harbor reads --ak values as JSON, so quote the commit id "
            "(--ak 'git_ref=\"<commit>\"') or give the full 40-character commit"
        )
    if bool(wheel_path) == bool(git_ref):
        raise AdapterConfigError(
            "give exactly one of wheel=<path to an openprogram wheel> or "
            "git_ref=<commit or tag>"
        )
    if wheel_path:
        return str(wheel_path)
    ref = git_ref.strip()
    if not ref or any(ch.isspace() for ch in ref):
        raise AdapterConfigError(f"git_ref must be a single commit or tag, got {git_ref!r}")
    return f"openprogram @ git+{git_url}@{ref}"


def wheel_remote_path(local_name: str) -> str:
    """Container path an uploaded wheel keeps its file name under.

    uv reads the version and tags from the wheel's file name, so the name
    must survive the upload unchanged.
    """
    name = PurePosixPath(local_name).name
    if not name.endswith(".whl"):
        raise AdapterConfigError(f"wheel must be a .whl file, got {local_name!r}")
    return str(WHEEL_DIR / name)


def install_command(package: str, *, uv_version: str = UV_VERSION,
                    python_version: str = PYTHON_VERSION,
                    constraints: PurePosixPath | str = CONSTRAINTS_PATH) -> str:
    """Shell command (run as root) that installs uv, Python and OpenProgram.

    Everything lands under :data:`INSTALL_ROOT`, readable by every user, so
    a task whose agent runs as a non-root user can still start it.
    ``UV_PYTHON_PREFERENCE=only-managed`` keeps the task image's own Python
    out of it: every task runs OpenProgram on the same interpreter.

    ``constraints`` (already in the container) holds every dependency to the
    version the commit's ``uv.lock`` resolved: a wheel's metadata only
    gives lower bounds, so without it each container would resolve the
    newest releases on PyPI at the time it starts. The versions installed
    are written to :data:`PACKAGES_FILE`.
    """
    root = shlex.quote(str(INSTALL_ROOT))
    installer = f"https://astral.sh/uv/{uv_version}/install.sh"
    return (
        "set -eu; "
        f"root={root}; "
        'mkdir -p "$root"; '
        f"curl -LsSf {shlex.quote(installer)} "
        '| env UV_INSTALL_DIR="$root/uv" UV_NO_MODIFY_PATH=1 sh; '
        'export UV_PYTHON_INSTALL_DIR="$root/python" UV_TOOL_DIR="$root/tools" '
        'UV_TOOL_BIN_DIR="$root/bin" UV_CACHE_DIR="$root/cache" '
        "UV_PYTHON_PREFERENCE=only-managed; "
        f'"$root/uv/uv" tool install --python {shlex.quote(python_version)} '
        f"--constraints {shlex.quote(str(constraints))} "
        f"{shlex.quote(package)}; "
        '"$root/uv/uv" pip freeze --python "$root/tools/openprogram/bin/python" '
        f"> {shlex.quote(str(PACKAGES_FILE))}; "
        'rm -rf "$root/cache"; '
        'chmod -R a+rX "$root"; '
        '"$root/bin/openprogram" --version'
    )


def bundle_install_command(bundle: PurePosixPath | str = BUNDLE_PATH, *,
                           dest: PurePosixPath | str = "/") -> str:
    """Shell command (run as root) that unpacks a prebuilt install, offline.

    The bundle is :data:`INSTALL_ROOT` as :func:`install_command` left it in
    a ``linux/amd64`` builder with an old glibc (uv, Python and OpenProgram
    with its locked dependencies), packed as ``installed-agent/openprogram``.
    Unpacked at the same path it runs unchanged, so a task whose network is
    closed during setup still gets OpenProgram, every trial runs the same
    bytes, and no trial downloads anything.
    """
    quoted = shlex.quote(str(bundle))
    base = PurePosixPath(dest)
    root = shlex.quote(str(base / INSTALL_ROOT.relative_to("/")))
    return (
        "set -eu; "
        f"rm -rf {root}; "
        f"tar -xzf {quoted} -C {shlex.quote(str(base))}; "
        f"rm -f {quoted}; "
        f"chmod -R a+rX {root}; "
        f"{shlex.quote(str(base / OPENPROGRAM.relative_to('/')))} --version"
    )


def openprogram_model(model_name: str | None, provider: str | None = None) -> str:
    """OpenProgram's ``PROVIDER/MODEL_ID`` for Harbor's ``-m`` value.

    The first path segment is the provider and the rest is the model id
    (``openrouter/moonshotai/kimi-k2`` is provider ``openrouter``, model
    ``moonshotai/kimi-k2``). ``provider`` replaces the first segment when
    OpenProgram names the provider differently from Harbor.
    """
    head, sep, model_id = (model_name or "").strip().partition("/")
    if not sep or not head.strip() or not model_id.strip():
        raise AdapterConfigError(
            f"-m must be PROVIDER/MODEL_ID, got {model_name!r}"
        )
    chosen = (provider or head).strip()
    if not chosen:
        raise AdapterConfigError("provider must not be empty")
    return f"{chosen}/{model_id.strip()}"


def key_env_name(provider: str, override: str | None = None) -> str:
    """The environment variable OpenProgram reads ``provider``'s key from in
    a ``--print`` run: ``override``, else its known name, else
    ``<PROVIDER>_API_KEY``."""
    if override:
        name = override.strip()
    else:
        name = _KEY_ENV.get(provider) or (
            re.sub(r"[^A-Za-z0-9]", "_", provider).upper() + "_API_KEY")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise AdapterConfigError(f"not an environment variable name: {name!r}")
    return name


#: What the adapter declares to Harbor (``AgentCapabilities``): its
#: transcript becomes an ATIF trajectory, it applies ``reasoning_effort``,
#: and it has web tools Harbor's ``disable_web_search`` turns off
#: (``--no-web``), as Harbor's Codex and Claude Code adapters declare.
CAPABILITIES = {"atif": True, "reasoning_effort": True, "native_web_tools": True}

#: Reasoning efforts Harbor passes as ``reasoning_effort``. ``openprogram
#: --effort`` takes each one; its ``none`` sends ``effort: "none"`` on an
#: OpenAI reasoning model that takes it, the same request Codex sends.
HARBOR_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def effort_flag(effort: Any) -> str:
    """The ``--effort`` value for Harbor's ``reasoning_effort``."""
    value = str(effort).strip().lower() if isinstance(effort, str) else ""
    if value not in HARBOR_EFFORTS:
        raise AdapterConfigError(
            f"reasoning_effort must be one of {', '.join(HARBOR_EFFORTS)}, got {effort!r}")
    return value


def usage_file(logs_dir: PurePosixPath | str = LOGS_DIR) -> str:
    return str(PurePosixPath(logs_dir) / USAGE_FILE_NAME)


def transcript_file(logs_dir: PurePosixPath | str = LOGS_DIR) -> str:
    return str(PurePosixPath(logs_dir) / TRANSCRIPT_FILE_NAME)


def run_command(model: str, *,
                logs_dir: PurePosixPath | str = LOGS_DIR,
                executable: PurePosixPath | str = OPENPROGRAM,
                effort: str | None = None,
                key_env: str | None = None,
                no_web: bool = False) -> str:
    """Shell command (run as the task's agent user, in its working
    directory) that runs one instruction and keeps OpenProgram's exit code.

    The instruction comes from :data:`INSTRUCTION_ENV` and reaches
    ``--print=-`` on stdin, so no text of it is ever read as a flag or
    split by the shell. ``key_env`` names the variable OpenProgram reads
    the provider's key from (:func:`key_env_name`): the key in
    :data:`API_KEY_ENV` is handed to that one process under that name, so
    nothing is stored. ``--standalone`` keeps the turn in this process and
    ``--no-project-binding`` writes nothing into the graded folder.
    ``effort`` (Harbor's ``reasoning_effort``) becomes ``--effort``;
    without it OpenProgram runs at its Agent's own effort. ``no_web``
    (Harbor's ``disable_web_search``) becomes ``--no-web``.

    Stdout is the run's ``stream-json`` events, copied to ``openprogram.txt``
    in the logs; stderr goes to ``openprogram.stderr.txt``. Core files are
    off, and the installed package versions are copied to
    ``openprogram-packages.txt``. OpenProgram's process id is kept in the
    logs directory, so :func:`stop_command` can stop the run when Harbor's
    agent timeout ends this command first.
    """
    logs = PurePosixPath(logs_dir)
    pid_file = shlex.quote(str(logs / PID_FILE_NAME))
    exe = shlex.quote(str(executable))
    key = (f'export {key_env_name("", key_env)}="${API_KEY_ENV}"; unset {API_KEY_ENV}; '
           if key_env else "")
    return (
        # A process that aborts never leaves a core file in the graded
        # working directory (one left 637 MB there).
        "ulimit -c 0; "
        "export OPENPROGRAM_NO_AUTO_UPDATE=1; "
        f"{key}"
        f"mkdir -p {shlex.quote(str(logs))}; "
        f"cp {shlex.quote(str(PACKAGES_FILE))} "
        f"{shlex.quote(str(logs / PACKAGES_FILE_NAME))} 2>/dev/null || true; "
        f"rm -f {pid_file}; "
        # In a subshell so the process id is OpenProgram's own and the exit
        # status is its exit status.
        f'( printf %s "${INSTRUCTION_ENV}" | {exe} '
        f"{PRINT_FLAG}- --standalone --no-project-binding --output-format stream-json "
        f"--model {shlex.quote(model)} "
        + (f"--effort {shlex.quote(effort_flag(effort))} " if effort else "")
        + ("--no-web " if no_web else "")
        + f"--usage-file {shlex.quote(usage_file(logs))} "
        f"--transcript-file {shlex.quote(transcript_file(logs))} "
        f"2> {shlex.quote(str(logs / STDERR_FILE_NAME))} & "
        f'echo "$!" > {pid_file}; wait "$!" ) '
        f"| tee {shlex.quote(str(logs / OUTPUT_FILE_NAME))}; "
        "status=$?; "
        f"rm -f {pid_file}; "
        'exit "$status"'
    )


def stop_command(*, logs_dir: PurePosixPath | str = LOGS_DIR,
                 grace_sec: int = STOP_GRACE_SEC) -> str:
    """Shell command (run as root) that ends a run Harbor stopped waiting for.

    When Harbor's agent timeout fires, the run command's shell may still be
    running in the container. This sends OpenProgram SIGTERM (it cancels
    the turn and writes the usage summary), waits up to ``grace_sec``
    seconds for it to exit, and kills it after that.
    """
    logs = PurePosixPath(logs_dir)
    pid_file = shlex.quote(str(logs / PID_FILE_NAME))
    return (
        f"if [ -s {pid_file} ]; then "
        f'pid=$(cat {pid_file}); kill -TERM "$pid" 2>/dev/null; waited=0; '
        f'while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt {int(grace_sec)} ]; '
        "do sleep 1; waited=$((waited + 1)); done; "
        'if kill -0 "$pid" 2>/dev/null; then kill -KILL "$pid" 2>/dev/null; fi; '
        f"rm -f {pid_file}; fi; "
        "true"
    )


def rebuild_transcript_command(*, logs_dir: PurePosixPath | str = LOGS_DIR,
                               executable: PurePosixPath | str = OPENPROGRAM) -> str:
    """Shell command (run as the agent user, after :func:`finish_command`)
    that writes the transcript of a run killed before it wrote its own.

    ``openprogram transcript`` reads the run back from the state the run
    left in the agent's home (the copy in the logs is the same, and stays
    untouched evidence) and builds the transcript with the code
    ``--transcript-file`` runs, so a killed run's transcript is the one a
    finished run writes, less what lived only in the process. A transcript
    already there is kept.
    """
    target = shlex.quote(transcript_file(logs_dir))
    stderr = shlex.quote(str(PurePosixPath(logs_dir) / STDERR_FILE_NAME))
    return (
        f"[ -s {target} ] || "
        f"{shlex.quote(str(executable))} transcript --output {target} 2>> {stderr}"
    )


def finish_command(*, logs_dir: PurePosixPath | str = LOGS_DIR) -> str:
    """Shell command (run as the agent user, after the run however it ended)
    that keeps OpenProgram's state as evidence and deletes the credential.

    Every entry of the state directory but :data:`STATE_NOT_COPIED` is
    copied to :data:`EVIDENCE_DIR_NAME` in the logs, which Harbor syncs to
    the trial folder: the session log holding the turn's journal (every
    tool result whole), the usage ledger with each request's tokens, the
    process records. A run killed outright writes neither the usage summary
    nor the transcript; this copy still holds what it did. The credential
    is never copied, and is deleted afterwards whatever the copy did, so a
    snapshot of the container or a shared job folder holds no key (a run
    reads its key from its environment and stores none, so there is
    normally none to delete). It runs as the agent user, whose home holds
    the state. Every state directory there is handled alike (the
    default one and any named profile's), and the command fails when a
    credential is still there afterwards.
    """
    dest = shlex.quote(str(PurePosixPath(logs_dir) / EVIDENCE_DIR_NAME))
    skipped = "|".join(STATE_NOT_COPIED)
    return (
        "left=0; "
        f"for state in {STATE_DIRS}; do "
        '[ -d "$state" ] && [ ! -L "$state" ] || continue; '
        # ~/.openprogram -> openprogram-state, ~/.openprogram-x -> openprogram-state-x
        f'dest={dest}"${{state##*/.openprogram}}"; '
        'rm -rf "$dest"; mkdir -p "$dest"; '
        'for entry in "$state"/* "$state"/.[!.]*; do '
        '[ -e "$entry" ] || [ -L "$entry" ] || continue; '
        f'case "${{entry##*/}}" in {skipped}) continue;; esac; '
        'cp -R "$entry" "$dest"/ 2>/dev/null || true; '
        "done; "
        'rm -rf "$state/auth" 2>/dev/null; '
        '[ ! -e "$state/auth" ] || left=1; '
        "done; "
        '[ "$left" = 0 ]'
    )


def harbor_matcher_applies(command: str) -> bool:
    """Whether Harbor's error matcher may type a failure of ``command``.

    Harbor's matcher reads a failed command's whole output for provider
    error words. The run command's output is the model's text and its
    tools' output, which can quote any of them ("rate limit", "invalid api
    key"), so its failure is typed from the usage summary
    (:func:`error_kind`) instead. As Harbor's own Codex and Claude Code
    adapters do, the matcher sees only what the harness itself reports: the
    install commands print OpenProgram's and the installer's own words, so
    they keep it.
    """
    return PRINT_FLAG not in command


async def run_attempt(*, turn: Callable[[], Awaitable[None]],
                      stop: Callable[[], Awaitable[None]],
                      finish: Callable[[], Awaitable[None]],
                      rebuild: Callable[[], Awaitable[None]],
                      warn: Callable[[str], None]) -> None:
    """One attempt in the container: run the turn, finish, rebuild.

    Harbor's agent timeout cancels the turn: ``stop`` then ends
    OpenProgram so it writes its usage summary. ``finish`` (keep the state
    as evidence, delete any credential) and then ``rebuild`` (the
    transcript of a run killed before it wrote one) run whatever ended the
    attempt: a success, a failed turn, the timeout. A failing finish or
    rebuild is passed to ``warn`` and never replaces the attempt's own
    outcome, which is what Harbor reports.
    """
    try:
        try:
            await turn()
        except asyncio.CancelledError:
            await stop()
            raise
    finally:
        try:
            await finish()
        except Exception as exc:  # noqa: BLE001 - the attempt's own outcome stands
            warn(f"OpenProgram state not copied out or credential not removed: {exc!r}")
        try:
            await rebuild()
        except Exception as exc:  # noqa: BLE001 - the attempt's own outcome stands
            warn(f"OpenProgram transcript not rebuilt from its state: {exc!r}")


def parse_usage(text: str | None) -> dict[str, Any] | None:
    """The usage summary as a dict, or ``None`` when it is not one."""
    try:
        value = json.loads(text or "")
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def run_failure(usage: dict[str, Any] | None) -> str | None:
    """Why a run that exited 0 still did not do its job, or ``None``.

    OpenProgram exits non-zero on a failed turn, but a zero exit is only
    trusted when the usage summary agrees: it exists, says the turn
    completed, and at least one model request was made.
    """
    if usage is None:
        return "OpenProgram exited 0 but wrote no usage summary"
    ended = usage.get("ended")
    if ended != ENDED_COMPLETED:
        error = usage.get("error")
        return f"OpenProgram run ended {ended!r}" + (f": {error}" if error else "")
    if not _count(usage.get("model_requests")):
        return "OpenProgram run completed without a single model request"
    return None


ENDED_NOT_STARTED = "not_started"

# Provider and network causes, by the error the usage summary records: a
# failed model-list request before the turn reads like "HTTP 429 for
# https://api.example.com" or "ConnectTimeout for ...", a request the
# provider refused in the turn like "Error code: 401 - {...}". Checked in
# order.
_STATUS = r"\b(?:HTTP|Error code:?|status:?)\s*"
_PROVIDER_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("authentication", re.compile(
        _STATUS + r"40[13]\b|unauthori[sz]ed|invalid[ _]api[ _]key|incorrect api key"
        r"|no api key|not logged in", re.IGNORECASE)),
    ("rate_limit", re.compile(_STATUS + r"429\b|rate.?limit|too many requests",
                              re.IGNORECASE)),
    ("server", re.compile(_STATUS + r"5\d\d\b", re.IGNORECASE)),
    ("network", re.compile(
        r"\b\w*(?:Connect|Timeout|SSL|Network|Protocol)\w*Error\b|"
        r"\bConnectTimeout\b|\bReadTimeout\b|\bTimeoutException\b|could not resolve",
        re.IGNORECASE)),
    ("model_not_found", re.compile(r"does not list the model", re.IGNORECASE)),
)

#: The error an idle bound gives a model stream that went silent, as the
#: turn reports it once its re-sends are spent: the shared bound says
#: ``no openai stream event for 300s`` (``response`` before the first
#: event); providers that bound their own stream name it in their words
#: (``no deepseek chunk event for 300s``, ``no deepseek response for 300s``,
#: ``no Codex WebSocket bytes for 600.0s``, ``no SSE data event for 300.0s``).
_STALLED = re.compile(r"\bno \S+(?: \S+){0,2} (?:event|response|bytes) for \d+(?:\.\d+)?s\b",
                      re.IGNORECASE)


#: What a failure ``--print`` classified where it caught it was, by the
#: category it recorded (``error_kind``): the stream idle or total bound,
#: a model the provider does not list, a refused credential. A memory
#: store that could not be read is OpenProgram's own failure (``None``).
RECORDED_KINDS: dict[str, str | None] = {
    "stream_idle": "stalled",
    "stream_total": "stalled",
    "model_unavailable": "model_not_found",
    "auth": "authentication",
    "memory_unavailable": None,
}
#: The same by the provider error reason it recorded (``ErrorReason``),
#: for a failure with no category of its own. ``transport`` is not here: a
#: stalled stream and a dropped connection both carry it, so the summary's
#: wording, or else ``network``, decides. ``unknown`` leaves it to the
#: wording.
RECORDED_REASONS: dict[str, str | None] = {
    "auth": "authentication",
    "authz": "authentication",
    "rate_limit": "rate_limit",
    "provider": "server",
    "blocked": "network",
    "timeout": "network",
    "invalid": None,
    "context": None,
    "policy": None,
}


def error_kind(usage: dict[str, Any] | None) -> str | None:
    """Why a run failed, when the cause is the provider or the network.

    ``stalled`` for a turn that ended on the stream idle bound once its
    re-sends were spent; otherwise ``authentication``, ``rate_limit``,
    ``server``, ``network`` or ``model_not_found``, before the turn or in
    it (a request the provider still refused after OpenProgram's own
    retries). ``None`` for the agent's own failure, a success, a stop, or a
    cause the summary does not name. Only OpenProgram's own usage summary is
    read, never the run's output, which holds the model's text.

    The classification ``--print`` recorded where it caught the exception
    is looked up first: its category (:data:`RECORDED_KINDS`), then the
    provider's reason (:data:`RECORDED_REASONS`). Only a build that records
    neither, a ``transport`` reason (stall or dropped connection) or an
    ``unknown`` one is read from the error's wording, which stays as the
    fallback until a Harbor trial shows both readings agree.
    """
    if not usage or usage.get("ended") not in (ENDED_NOT_STARTED, "failed"):
        return None
    kind = str(usage.get("error_kind") or "")
    if kind in RECORDED_KINDS:
        return RECORDED_KINDS[kind]
    reason = str(usage.get("error_reason") or "")
    if reason in RECORDED_REASONS:
        return RECORDED_REASONS[reason]
    error = str(usage.get("error") or "")
    if usage.get("ended") == "failed" and _STALLED.search(error):
        return "stalled"
    if reason == "transport":
        return "network"
    if reason and reason != "unknown":
        # Classified, and no table names it: not a provider or network cause.
        return None
    return next((cause for cause, pattern in _PROVIDER_KINDS if pattern.search(error)), None)


def context_fields(usage: dict[str, Any]) -> dict[str, Any]:
    """``AgentContext`` fields from OpenProgram's usage summary.

    OpenProgram counts uncached input, cache reads and cache writes apart;
    Harbor's ``n_input_tokens`` is all input including cache, and
    ``n_cache_tokens`` the cache reads. ``cost_usd`` stays ``None`` when
    OpenProgram could not price a request, so Harbor and the leaderboard
    price the tokens themselves instead of reading a partial sum.
    """
    by_model: dict[str, dict[str, Any]] = {}
    for entry in usage.get("by_model") or []:
        name = entry.get("model") or "unknown"
        current = by_model.setdefault(name, {
            "n_input_tokens": 0, "n_cache_tokens": 0, "n_output_tokens": 0,
            "cost_usd": 0.0,
        })
        current["n_input_tokens"] += _all_input(entry)
        current["n_cache_tokens"] += _count(entry.get("cached_input_tokens"))
        current["n_output_tokens"] += _count(entry.get("output_tokens"))
        current["cost_usd"] = _add_cost(current["cost_usd"], entry.get("cost_usd"))
    return {
        "n_input_tokens": _all_input(usage),
        "n_cache_tokens": _count(usage.get("cached_input_tokens")),
        "n_output_tokens": _count(usage.get("output_tokens")),
        "cost_usd": _cost(usage.get("cost_usd")),
        "model_usage": by_model or None,
        "metadata": {
            "openprogram": {
                key: usage.get(key)
                for key in (
                    "ended", "error", "phase", "error_reason", "error_kind",
                    "model", "effort_requested", "effort", "tools_count",
                    "session_id", "execution_id",
                    "duration_s", "cache_write_tokens", "unpriced_requests",
                    "model_requests", "agent_rounds", "tool_calls",
                    # Requests by what they were for: a compaction's
                    # (``summarize``) cost apart from the turn's rounds.
                    "by_call_kind",
                )
            },
        },
    }


def append_attempt(ledger: str | Path, *, trial: str, attempt: str,
                   fields: dict[str, Any], agent: str = "openprogram",
                   error_kind: str | None = None) -> None:
    """Record one attempt's tokens and cost in a ledger outside the trial.

    Harbor deletes a trial's directory before it retries the trial (and
    ``harbor job resume -f`` deletes the ones it reruns), so the cost of
    those attempts survives only here. One JSON object per line;
    ``attempt`` is unique per agent instance, and a reader keeps the last
    line of each attempt. ``error_kind`` is :func:`error_kind` of the
    attempt's summary, so a retried attempt says why it was retried.
    """
    meta = (fields.get("metadata") or {}).get("openprogram") or {}
    line = json.dumps({
        "trial": trial, "attempt": attempt, "agent": agent, "error_kind": error_kind,
        "cost_usd": fields.get("cost_usd"),
        "n_input_tokens": fields.get("n_input_tokens"),
        "n_cache_tokens": fields.get("n_cache_tokens"),
        "n_output_tokens": fields.get("n_output_tokens"),
        "ended": meta.get("ended"), "error": meta.get("error"),
    }, ensure_ascii=False)
    path = Path(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    # One write in append mode: concurrent trials in Harbor's process never
    # interleave inside a line.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_attempts(ledger: str | Path) -> dict[str, dict[str, Any]]:
    """Every attempt in a ledger by its id, the last line of each winning."""
    attempts: dict[str, dict[str, Any]] = {}
    try:
        text = Path(ledger).read_text(encoding="utf-8")
    except OSError:
        return attempts
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("attempt") and entry.get("trial"):
            attempts[str(entry["attempt"])] = entry
    return attempts


def parse_transcript(text: str | None) -> list[dict[str, Any]]:
    """The transcript's records; a line that is not a JSON object is skipped."""
    records = []
    for line in (text or "").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def trajectory(records: list[dict[str, Any]], usage: dict[str, Any] | None, *,
               agent_version: str = "unknown") -> dict[str, Any] | None:
    """Harbor's trajectory (ATIF) for the run, from its transcript.

    The instruction is the user step; each of the turn's model requests is
    an agent step with its tokens, the text and reasoning it returned, its
    tool calls, and their results as the step's observation. Requests
    beside the turn (a title, a summary) are counted in ``final_metrics``
    only. ``None`` when the transcript holds no instruction.
    """
    run = next((r for r in records if r.get("type") == "run"), {})
    usage = usage or {}
    model = run.get("model") or usage.get("model")
    steps: list[dict[str, Any]] = []
    other_requests = 0

    def agent_step(timestamp: str | None = None) -> dict[str, Any]:
        step = {"step_id": len(steps) + 1, "source": "agent", "message": "",
                "model_name": model}
        if timestamp:
            step["timestamp"] = timestamp
        steps.append(step)
        return step

    current: dict[str, Any] | None = None
    for record in records:
        kind = record.get("type")
        if kind == "user":
            steps.append({
                "step_id": len(steps) + 1, "source": "user",
                "message": str(record.get("text") or ""),
                **({"timestamp": record["time"]} if record.get("time") else {}),
                **({"extra": {"run_note": run["run_note"]}} if run.get("run_note") else {}),
            })
            current = None
        elif kind == "request":
            if record.get("call_kind") not in (None, "chat"):
                other_requests += 1
                continue
            current = agent_step(record.get("time"))
            current["model_name"] = record.get("model") or model
            input_tokens = (_count(record.get("input_tokens"))
                            + _count(record.get("cached_input_tokens"))
                            + _count(record.get("cache_write_tokens")))
            current["metrics"] = {
                "prompt_tokens": input_tokens,
                "completion_tokens": _count(record.get("output_tokens")),
                "cached_tokens": _count(record.get("cached_input_tokens")),
            }
        elif kind in ("assistant_text", "reasoning", "tool_call", "tool_result"):
            if not steps:
                continue
            if current is None:
                current = agent_step()
            if kind == "assistant_text":
                text = str(record.get("text") or "")
                current["message"] = (f"{current['message']}\n\n{text}"
                                      if current["message"] else text)
            elif kind == "reasoning":
                text = str(record.get("text") or "")
                previous = current.get("reasoning_content")
                current["reasoning_content"] = f"{previous}\n\n{text}" if previous else text
            elif kind == "tool_call":
                arguments = record.get("arguments")
                current.setdefault("tool_calls", []).append({
                    "tool_call_id": str(record.get("tool_call_id") or ""),
                    "function_name": str(record.get("name") or ""),
                    "arguments": arguments if isinstance(arguments, dict)
                    else {"input": arguments},
                })
            else:
                content = record.get("content")
                current.setdefault("observation", {"results": []})["results"].append({
                    "source_call_id": record.get("tool_call_id"),
                    "content": content if isinstance(content, str)
                    else json.dumps(content, ensure_ascii=False, default=str),
                    "extra": {"is_error": bool(record.get("is_error")),
                              "time": record.get("time"),
                              **({"complete": bool(record["complete"])}
                                 if "complete" in record else {})},
                })
    if not any(step["source"] == "user" for step in steps):
        return None
    return {
        "schema_version": "ATIF-v1.7",
        "session_id": run.get("session_id"),
        "agent": {"name": "openprogram", "version": agent_version, "model_name": model},
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": _all_input(usage),
            "total_completion_tokens": _count(usage.get("output_tokens")),
            "total_cached_tokens": _count(usage.get("cached_input_tokens")),
            "total_cost_usd": _cost(usage.get("cost_usd")),
            "total_steps": len(steps),
            "extra": {"other_requests": other_requests,
                      "ended": usage.get("ended", run.get("ended"))},
        },
    }


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _cost(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _add_cost(total: float | None, value: Any) -> float | None:
    cost = _cost(value)
    if total is None or cost is None:
        return None
    return total + cost


def _all_input(entry: dict[str, Any]) -> int:
    return (_count(entry.get("input_tokens"))
            + _count(entry.get("cached_input_tokens"))
            + _count(entry.get("cache_write_tokens")))


__all__ = [
    "API_KEY_ENV", "AdapterConfigError", "BUNDLE_PATH", "BUNDLE_USABLE_CHECK", "CAPABILITIES",
    "CONSTRAINTS_PATH", "DEFAULT_GIT_URL", "EVIDENCE_DIR_NAME", "HARBOR_EFFORTS",
    "INSTRUCTION_ENV", "PRINT_FLAG",
    "RECORDED_KINDS", "RECORDED_REASONS", "effort_flag",
    "bundle_install_command", "error_kind", "finish_command", "harbor_matcher_applies",
    "run_attempt",
    "INSTALL_ROOT", "LOGS_DIR", "OPENPROGRAM", "OUTPUT_FILE_NAME",
    "PACKAGES_FILE", "PACKAGES_FILE_NAME", "PID_FILE_NAME", "PYTHON_VERSION",
    "STDERR_FILE_NAME", "STOP_GRACE_SEC", "TRAJECTORY_FILE_NAME",
    "TRANSCRIPT_FILE_NAME", "USAGE_FILE_NAME", "UV_VERSION", "append_attempt",
    "context_fields", "read_attempts",
    "install_command", "key_env_name", "openprogram_model",
    "package_spec", "parse_transcript", "parse_usage", "rebuild_transcript_command",
    "run_command", "run_failure",
    "stop_command", "trajectory", "transcript_file", "usage_file", "wheel_remote_path",
]
