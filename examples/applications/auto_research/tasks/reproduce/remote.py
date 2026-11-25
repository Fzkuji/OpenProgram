"""Run one experiment on another machine: a GPU box over ssh, or a cluster through Slurm,
as Bohrium and HPC users do by hand. The remote checkout moves to the commit being run,
so the metric belongs to committed code. Plain code, no model calls.
"""

import json
import re
import shlex
import subprocess
import time
from pathlib import Path

from openprogram.runtime.agent.call_state import check_cancelled

from auto_research.trail import Trail

from . import common

SCHEDULERS = ("ssh", "slurm")
SSH = ("ssh", "-o", "BatchMode=yes")
RUN_TIMEOUT = 7 * 24 * 3600     # one ssh run
CALL_TIMEOUT = 300              # one short remote command (sbatch, squeue, scp)
POLL = 30                       # seconds between Slurm status checks
MAX_WAIT = 7 * 24 * 3600
RUN_FOLDER = ".openprogram_runs"
DONE_STATES = ("COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "PREEMPTED",
               "BOOT_FAIL", "DEADLINE")


def remote_run(paper_dir: str, host: str, command: str = "", scheduler: str = "ssh", out_dir: str = "") -> str:
    """Run the experiment on a remote machine and read its metric.

    paper_dir is the project's Git repository; its HEAD commit is what runs, so push it
    where the remote checkout can fetch it first. host is an ssh destination (user@host or
    an alias from ~/.ssh/config, key login only). The remote checkout is experiment.md's
    Remote field. command defaults to experiment.md's Command. scheduler "ssh" runs
    `cd <Remote> && git fetch && git checkout <commit> && <command>` over ssh and reads the
    output; "slurm" does the checkout, submits the command with `sbatch --wrap`, polls
    squeue and sacct until the job ends, and copies the job's log back with scp. The
    metric is the last `<Metric>: <number>` of the log. FAIL when the run fails or prints
    no metric. The log, report.md and the trail go to out_dir/<timestamp>/ (default
    <paper_dir>/drafts/remote_run). results.tsv is not written.
    """
    paper = common.repo(paper_dir)
    values = common.spec(paper, need=("Metric", "Remote") if command.strip() else ("Command", "Metric", "Remote"))
    if scheduler not in SCHEDULERS:
        raise ValueError(f"scheduler must be one of {', '.join(SCHEDULERS)}, not {scheduler!r}")
    host = host.strip()
    if not host or host.startswith("-") or re.search(r"\s", host):
        raise ValueError(f"host {host!r} is not an ssh destination")
    command = command.strip() or values["Command"]
    folder = values["Remote"]
    commit = common.head(paper)
    out = common.run_folder(paper, out_dir, "remote_run")
    trail = Trail("remote_run", {"paper_dir": str(paper), "host": host, "command": command,
                                 "scheduler": scheduler, "out_dir": out_dir}, {})
    dirty = common.dirty(paper)
    trail.step("prepare", commit=commit, remote=folder, dirty=dirty)
    check_cancelled()
    if scheduler == "ssh":
        result = over_ssh(host, folder, commit, command, out)
    else:
        result = over_slurm(host, folder, commit, command, out, trail)
    metric = common.metric_from(result["output"], values["Metric"]) if result["output"] else None
    ok = result["ok"] and metric is not None
    note = result["note"] or ("" if metric is not None else f"日志里没有 `{values['Metric']}: <数字>`。")
    trail.step("result", ok=ok, metric=metric, seconds=result["seconds"])
    status = "PASS" if ok else "FAIL"
    data = {"status": status, "host": host, "scheduler": scheduler, "remote": folder, "commit": commit,
            "command": command, "metric_name": values["Metric"], "metric": metric, "seconds": result["seconds"],
            "job": result.get("job", ""), "state": result.get("state", ""), "log": result["log"], "note": note,
            "dirty": dirty}
    (out / "report.md").write_text(report(data), encoding="utf-8")
    run = trail.save(out, report=out / "report.md", log=result["log"])
    return json.dumps({"status": status, "report": str(out / "report.md"), "run": str(run), "metric": metric,
                       "commit": commit, "log": result["log"], "job": result.get("job", ""),
                       "state": result.get("state", ""), "seconds": result["seconds"], "note": note},
                      ensure_ascii=False)


def cd(folder: str) -> str:
    """cd into the remote folder; ~/ stays unquoted so the remote shell expands it."""
    if folder == "~":
        return "cd ~"
    if folder.startswith("~/"):
        return f"cd ~/{shlex.quote(folder[2:])}"
    return f"cd {shlex.quote(folder)}"


def checkout(folder: str, commit: str) -> str:
    return f"{cd(folder)} && git fetch --quiet && git checkout --quiet {shlex.quote(commit)}"


def call(args: list[str], timeout: int) -> tuple[int | None, str, str]:
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return None, _text(e.stdout), _text(e.stderr)
    except OSError as e:
        return 127, "", str(e)
    return done.returncode, done.stdout or "", done.stderr or ""


def _text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def over_ssh(host: str, folder: str, commit: str, command: str, out: Path) -> dict:
    script = f"{checkout(folder, commit)} && {command}"
    start = time.monotonic()
    code, stdout, stderr = call([*SSH, host, script], RUN_TIMEOUT)
    secs = round(time.monotonic() - start, 1)
    log = out / "remote.log"
    log.write_text(f"$ ssh {host} {script}\n{stdout}\n{stderr}\n[exit {code}]\n", encoding="utf-8")
    note = "" if code == 0 else "ssh 超时。" if code is None else f"远端退出码 {code}：{stderr.strip()[-300:]}"
    return {"ok": code == 0, "output": stdout + "\n" + stderr, "seconds": secs, "log": str(log), "note": note}


def over_slurm(host: str, folder: str, commit: str, command: str, out: Path, trail: Trail) -> dict:
    log = out / "remote.log"
    submit = (f"{checkout(folder, commit)} && mkdir -p {RUN_FOLDER} && sbatch --parsable "
              f"--output={RUN_FOLDER}/slurm-%j.out --wrap={shlex.quote(command)}")
    code, stdout, stderr = call([*SSH, host, submit], CALL_TIMEOUT)
    job = (stdout.strip().splitlines() or [""])[-1].split(";")[0].strip()
    if code != 0 or not job.isdigit():
        log.write_text(f"$ ssh {host} {submit}\n{stdout}\n{stderr}\n[exit {code}]\n", encoding="utf-8")
        return {"ok": False, "output": "", "seconds": None, "log": str(log),
                "note": f"sbatch 提交失败：{(stderr or stdout).strip()[-300:]}"}
    trail.step("submit", job=job)
    start = time.monotonic()
    state = ""
    while True:
        check_cancelled()
        code, stdout, _ = call([*SSH, host, f"squeue -h -j {job} -o %T"], CALL_TIMEOUT)
        if code == 0 and not stdout.strip():
            break
        if time.monotonic() - start > MAX_WAIT:
            state = "WAIT_LIMIT"
            break
        time.sleep(POLL)
    if not state:
        code, stdout, _ = call([*SSH, host, f"sacct -n -X -P -j {job} -o State,ExitCode"], CALL_TIMEOUT)
        line = (stdout.strip().splitlines() or [""])[0]
        state = line.split("|")[0].split()[0] if line.strip() else "UNKNOWN"
    secs = round(time.monotonic() - start, 1)
    trail.step("wait", job=job, state=state)
    remote_log = f"{folder.rstrip('/')}/{RUN_FOLDER}/slurm-{job}.out"
    if remote_log.startswith("~/"):
        remote_log = remote_log[2:]
    code, _, stderr = call(["scp", "-q", "-o", "BatchMode=yes", f"{host}:{remote_log}", str(log)], CALL_TIMEOUT)
    output = log.read_text(encoding="utf-8", errors="ignore") if code == 0 and log.is_file() else ""
    note = ""
    if code != 0:
        log.write_text(f"scp {host}:{remote_log} failed: {stderr}\n", encoding="utf-8")
        note = f"取回日志失败：{stderr.strip()[-300:]}"
    elif state != "COMPLETED":
        note = f"作业 {job} 结束状态 {state}。"
    return {"ok": state == "COMPLETED" and code == 0, "output": output, "seconds": secs, "log": str(log),
            "note": note, "job": job, "state": state}


def report(d: dict) -> str:
    how = "ssh 直接运行" if d["scheduler"] == "ssh" else f"Slurm 作业 {d['job'] or '—'}（{d['state'] or '—'}）"
    lines = ["# 远程运行", "", f"结论：**{d['status']}**，{d['metric_name']} = {common.fmt(d['metric'])}", "",
             f"- 机器：`{d['host']}`，目录 `{d['remote']}`，{how}",
             f"- 提交：`{d['commit'][:12]}`", f"- 命令：`{d['command']}`",
             f"- 用时：{common.fmt(d['seconds'])}s", f"- 日志：`{Path(d['log']).name}`"]
    if d["dirty"]:
        lines.append("- 本地有未提交的改动：远端只跑已提交并推送的代码，这些改动不在里面。")
    if d["note"]:
        lines.append(f"- {d['note']}")
    lines += ["", "结果没有写进 results.tsv；要记录就按这次的提交和指标加一行。", ""]
    return "\n".join(lines)


__all__ = ["remote_run"]
