"""The Harbor adapter's shell commands, run by bash against a stand-in CLI.

Harbor runs each command as ``bash -c "set -o pipefail; <command>"``; the
stand-in ``openprogram`` records its arguments, its stdin and the key
variables it sees, and exits with a chosen code.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from scripts.benchmarks.harbor_agent import commands

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

FAKE_CLI = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_RECORD/argv"
if [ "$1" = transcript ]; then
  echo '{"type": "run", "rebuilt": true}' > "$3"; exit "${FAKE_REBUILD_EXIT:-0}"
fi
cat > "$FAKE_RECORD/stdin"
printf '%s|%s' "${DEEPSEEK_API_KEY:-}" "${OPENPROGRAM_HARBOR_API_KEY:-unset}" \\
  > "$FAKE_RECORD/key"
ulimit -c > "$FAKE_RECORD/core_limit"
if [ -n "$FAKE_SLOW" ]; then
  trap 'echo "{\\"ended\\": \\"cancelled\\"}" > "$FAKE_SLOW"; exit 130' TERM
  while :; do sleep 0.05; done
fi
echo "the reply"
echo "[error] on stderr" >&2
exit "${FAKE_EXIT:-0}"
"""

INSTRUCTION = "-x 'quoted' \"$HOME\"\nsecond line"


@pytest.fixture
def sandbox(tmp_path: Path):
    home = tmp_path / "home"
    work = tmp_path / "work"
    logs = tmp_path / "logs"
    record = tmp_path / "record"
    for folder in (home, work, record):
        folder.mkdir()
    cli = tmp_path / "openprogram"
    cli.write_text(FAKE_CLI)
    cli.chmod(0o755)
    return {"home": home, "work": work, "logs": logs, "record": record, "cli": cli}


def _env(sandbox, **env: str) -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(sandbox["home"]), "FAKE_RECORD": str(sandbox["record"]),
            commands.INSTRUCTION_ENV: INSTRUCTION, **env}


def _bash(command: str, *, cwd: Path, sandbox, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"set -o pipefail; {command}"],
        cwd=cwd, capture_output=True, text=True, timeout=30, env=_env(sandbox, **env),
    )


def _command(sandbox, **kwargs) -> str:
    return commands.run_command(
        "deepseek/deepseek-chat", logs_dir=str(sandbox["logs"]),
        executable=str(sandbox["cli"]), **kwargs)


def _run(sandbox, *, cwd: Path | None = None, exit_code: int = 0, **kwargs):
    return _bash(_command(sandbox, **kwargs), cwd=cwd or sandbox["work"], sandbox=sandbox,
                 FAKE_EXIT=str(exit_code))


def test_run_pipes_the_instruction_intact_and_keeps_the_streams_apart(sandbox):
    result = _run(sandbox)

    assert result.returncode == 0
    assert (sandbox["record"] / "stdin").read_text() == INSTRUCTION
    argv = (sandbox["record"] / "argv").read_text()
    assert argv.startswith("--print=-\n--standalone\n--no-project-binding\n")
    assert INSTRUCTION not in argv
    assert "--model\ndeepseek/deepseek-chat\n" in argv
    assert f"--usage-file\n{sandbox['logs']}/openprogram-usage.json\n" in argv
    assert (f"--transcript-file\n{sandbox['logs']}/openprogram-transcript.jsonl\n"
            in argv)
    # stdout is the events, stderr its own file.
    assert (sandbox["logs"] / "openprogram.txt").read_text() == "the reply\n"
    assert (sandbox["logs"] / commands.STDERR_FILE_NAME).read_text() == \
        "[error] on stderr\n"


def test_the_key_reaches_the_run_under_the_providers_name_only(sandbox):
    result = _bash(_command(sandbox, key_env="DEEPSEEK_API_KEY"), cwd=sandbox["work"],
                   sandbox=sandbox, **{commands.API_KEY_ENV: "sk-test-value"})

    assert result.returncode == 0
    assert (sandbox["record"] / "key").read_text() == "sk-test-value|unset"
    assert "sk-test-value" not in (sandbox["record"] / "argv").read_text()
    # Nothing is stored in the home or the working folder.
    assert not any(sandbox["home"].iterdir()) and not any(sandbox["work"].iterdir())


def test_no_web_reaches_the_run(sandbox):
    _run(sandbox, no_web=True)
    assert "--no-web\n" in (sandbox["record"] / "argv").read_text()


@pytest.mark.parametrize("exit_code", [1, 130])
def test_run_keeps_openprogram_exit_code(sandbox, exit_code):
    assert _run(sandbox, exit_code=exit_code).returncode == exit_code


def test_run_writes_no_core_file(sandbox):
    # A process that aborts at shutdown once left a 637 MB core file in the
    # graded working directory.
    _run(sandbox)

    assert (sandbox["record"] / "core_limit").read_text().strip() == "0"


def test_stop_after_harbor_timeout_lets_openprogram_write_its_summary(sandbox):
    """Harbor's agent timeout: the run command is abandoned, the stop command
    sends OpenProgram SIGTERM and waits for it."""
    usage = sandbox["logs"] / "openprogram-usage.json"
    running = subprocess.Popen(
        ["bash", "-c", f"set -o pipefail; {_command(sandbox)}"], cwd=sandbox["work"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=_env(sandbox, FAKE_SLOW=str(usage)),
    )
    try:
        pid_file = sandbox["logs"] / commands.PID_FILE_NAME
        deadline = time.monotonic() + 10
        while not ((sandbox["record"] / "core_limit").exists() and pid_file.exists()) \
                and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pid_file.exists()

        stop = _bash(commands.stop_command(logs_dir=str(sandbox["logs"]), grace_sec=10),
                     cwd=sandbox["home"], sandbox=sandbox)

        assert stop.returncode == 0
        assert '"cancelled"' in usage.read_text()
        assert running.wait(timeout=10) == 130
    finally:
        if running.poll() is None:
            running.kill()


def test_stop_without_a_run_does_nothing(sandbox):
    stop = _bash(commands.stop_command(logs_dir=str(sandbox["logs"]), grace_sec=1),
                 cwd=sandbox["home"], sandbox=sandbox)

    assert stop.returncode == 0


def _rebuild(sandbox, **env):
    command = commands.rebuild_transcript_command(
        logs_dir=str(sandbox["logs"]), executable=str(sandbox["cli"]))
    return _bash(command, cwd=sandbox["home"], sandbox=sandbox, **env)


def test_a_missing_transcript_is_rebuilt_from_the_state(sandbox):
    sandbox["logs"].mkdir()
    result = _rebuild(sandbox)

    assert result.returncode == 0, result.stderr
    transcript = sandbox["logs"] / commands.TRANSCRIPT_FILE_NAME
    assert '"rebuilt": true' in transcript.read_text()
    assert (sandbox["record"] / "argv").read_text().split() == [
        "transcript", "--output", str(transcript)]


def test_a_transcript_the_run_wrote_is_kept(sandbox):
    sandbox["logs"].mkdir()
    transcript = sandbox["logs"] / commands.TRANSCRIPT_FILE_NAME
    transcript.write_text('{"type": "run"}\n')

    assert _rebuild(sandbox).returncode == 0
    assert transcript.read_text() == '{"type": "run"}\n'
    assert not (sandbox["record"] / "argv").exists()


def test_a_failed_rebuild_fails_the_command(sandbox):
    sandbox["logs"].mkdir()
    assert _rebuild(sandbox, FAKE_REBUILD_EXIT="1").returncode == 1


def _bundle(tmp_path: Path, version: str) -> Path:
    """A bundle laid out as ``frontier_eval`` packs one."""
    import tarfile

    src = tmp_path / f"src-{version}"
    exe = src / "installed-agent" / "openprogram" / "bin" / "openprogram"
    exe.parent.mkdir(parents=True)
    exe.write_text(f"#!/bin/sh\necho openprogram {version}\n")
    exe.chmod(0o700)
    bundle = tmp_path / f"bundle-{version}.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(src / "installed-agent", arcname="installed-agent")
    return bundle


def test_a_bundle_installs_offline_and_replaces_an_earlier_install(sandbox, tmp_path):
    dest = tmp_path / "root"
    stale = dest / "installed-agent" / "openprogram" / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("from an earlier install")
    bundle = _bundle(tmp_path, "9.9.9")

    result = _bash(commands.bundle_install_command(bundle, dest=dest),
                   cwd=sandbox["work"], sandbox=sandbox)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "openprogram 9.9.9"
    assert not stale.exists() and not bundle.exists()
    exe = dest / "installed-agent" / "openprogram" / "bin" / "openprogram"
    assert exe.stat().st_mode & 0o055 == 0o055  # readable and runnable by the agent user


def test_a_broken_bundle_fails_the_install(sandbox, tmp_path):
    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"not a tarball")
    result = _bash(commands.bundle_install_command(bundle, dest=tmp_path / "root"),
                   cwd=sandbox["work"], sandbox=sandbox)
    assert result.returncode != 0


def _state(sandbox) -> Path:
    """OpenProgram's state in the agent's home, as a run leaves it."""
    state = sandbox["home"] / ".openprogram"
    for path, text in {
        "auth/deepseek/default.json": '{"auth_value": "sk-test-value"}',
        "cache/models.json": "{}",
        "projects/p1/chats/c1/log.jsonl": '{"journal": true}\n',
        "usage.db": "ledger",
        "config.json": "{}",
    }.items():
        (state / path).parent.mkdir(parents=True, exist_ok=True)
        (state / path).write_text(text)
    return state


def test_finish_keeps_the_state_as_evidence_and_removes_the_credential(sandbox):
    state = _state(sandbox)

    result = _bash(commands.finish_command(logs_dir=str(sandbox["logs"])),
                   cwd=sandbox["work"], sandbox=sandbox)

    assert result.returncode == 0, result.stderr
    evidence = sandbox["logs"] / commands.EVIDENCE_DIR_NAME
    assert (evidence / "projects/p1/chats/c1/log.jsonl").read_text() == '{"journal": true}\n'
    assert (evidence / "usage.db").read_text() == "ledger"
    assert not (evidence / "auth").exists() and not (evidence / "cache").exists()
    assert "sk-test-value" not in "".join(
        p.read_text() for p in evidence.rglob("*") if p.is_file())
    assert not (state / "auth").exists()
    assert (state / "projects").is_dir()


def test_finish_after_the_run_was_killed_outright(sandbox):
    """A run killed with SIGKILL writes neither the usage summary nor the
    transcript; its state is still copied out and the credential removed."""
    state = _state(sandbox)
    running = subprocess.Popen(
        ["bash", "-c", f"set -o pipefail; {_command(sandbox)}"], cwd=sandbox["work"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=_env(sandbox, FAKE_SLOW=str(sandbox["record"] / "never-written")),
    )
    try:
        pid_file = sandbox["logs"] / commands.PID_FILE_NAME
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        os.kill(int(pid_file.read_text().strip()), 9)
        assert running.wait(timeout=10) == 137

        result = _bash(commands.finish_command(logs_dir=str(sandbox["logs"])),
                       cwd=sandbox["work"], sandbox=sandbox)
    finally:
        if running.poll() is None:
            running.kill()

    assert result.returncode == 0, result.stderr
    assert not (sandbox["logs"] / "openprogram-usage.json").exists()
    evidence = sandbox["logs"] / commands.EVIDENCE_DIR_NAME
    assert (evidence / "projects/p1/chats/c1/log.jsonl").is_file()
    assert not (state / "auth").exists()


def test_finish_finds_the_credential_of_a_named_profile(sandbox):
    """OPENPROGRAM_PROFILE=x puts the state in ~/.openprogram-x; the finish
    does not depend on seeing the variable to find it."""
    credential = sandbox["home"] / ".openprogram-x" / "auth" / "openai" / "default.json"
    credential.parent.mkdir(parents=True)
    credential.write_text('{"auth_value": "sk-test-value"}')
    (sandbox["home"] / ".openprogram-x" / "usage.db").write_text("ledger")

    result = _bash(commands.finish_command(logs_dir=str(sandbox["logs"])),
                   cwd=sandbox["work"], sandbox=sandbox)

    assert result.returncode == 0, result.stderr
    assert not credential.exists()
    evidence = sandbox["logs"] / f"{commands.EVIDENCE_DIR_NAME}-x"
    assert (evidence / "usage.db").read_text() == "ledger"
    assert not (evidence / "auth").exists()


def test_finish_fails_when_a_credential_is_left(sandbox):
    state = _state(sandbox)
    (state / "auth").chmod(0o500)  # rm cannot empty it
    try:
        result = _bash(commands.finish_command(logs_dir=str(sandbox["logs"])),
                       cwd=sandbox["work"], sandbox=sandbox)
    finally:
        (state / "auth").chmod(0o700)

    assert result.returncode != 0


def test_finish_without_any_state_does_nothing(sandbox):
    result = _bash(commands.finish_command(logs_dir=str(sandbox["logs"])),
                   cwd=sandbox["work"], sandbox=sandbox)

    assert result.returncode == 0, result.stderr
    assert not (sandbox["logs"] / commands.EVIDENCE_DIR_NAME).exists()
