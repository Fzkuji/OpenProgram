"""The connection methods commands run through, ``ssh`` and ``docker``, as machine kinds.

Their settings, check, labels and the refusal of an SSH target that is this
computer. How commands run through them is ``openprogram.host.backend``'s
(``SHELLS``); the kinds registry loads this module (``kinds._load``).
"""
from __future__ import annotations

import subprocess

from openprogram.host.backend import DEFAULT_DOCKER_IMAGE
from openprogram.host.backend.ssh import SshBackend
from openprogram.host.machines import here
from openprogram.host.machines.kinds import Kind, KindError, register


def _docker_settings(settings: dict) -> dict:
    return {**settings, "image": str(settings.get("image") or "").strip() or DEFAULT_DOCKER_IMAGE}


def _check_ssh(machine) -> tuple[bool, str]:
    result = SshBackend(machine.connection.get("target"), machine.connection.get("workdir")).run("pwd", timeout=20)
    if result.exit_code == 0:
        lines = result.stdout.strip().splitlines()
        return True, "Connected" + (f" · {lines[-1]}" if lines else "")
    return False, (result.stderr.strip() or f"ssh exited {result.exit_code}")[:500]


def _check_docker(machine) -> tuple[bool, str]:
    try:
        proc = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                              capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return False, "Docker is not installed (docker CLI not on PATH)"
    except subprocess.TimeoutExpired:
        return False, "Docker did not answer within 20s"
    if proc.returncode != 0:
        return False, (proc.stderr.strip() or "Docker is not running")[:500]
    return True, f"Docker {proc.stdout.strip()} · image {machine.connection.get('image')}"


def ssh_host(target: str) -> str:
    """The host an SSH target names: ``user@host``, ``ssh://user@host:port`` or a bare host."""
    text = str(target or "").strip()
    if text.lower().startswith("ssh://"):
        from urllib.parse import urlsplit
        return urlsplit(text).hostname or ""
    return text.rsplit("@", 1)[-1].strip("[]")


def _ssh_port(target: str) -> int | None:
    """The port an ``ssh://`` target names, or None."""
    if not target.lower().startswith("ssh://"):
        return None
    from urllib.parse import urlsplit
    try:
        return urlsplit(target).port
    except ValueError:
        return None


def _admit_ssh(settings: dict) -> None:
    target = str(settings.get("target") or "")
    if _ssh_port(target) not in (None, 22):
        # Another port is another sshd: on a loopback address, a virtual machine or a container whose port is
        # forwarded here, each with its own operating system.
        return
    if here.is_this_computer(ssh_host(target)):
        raise KindError(f"Refused: the SSH target {target} is this computer, which is built in and runs "
                        "commands without being added as a machine.")


def _ssh_where(settings: dict) -> str:
    """The host, with the folder a conversation starts in when one is set: ``me@devbox:/srv/app``."""
    target, workdir = str(settings.get("target") or ""), str(settings.get("workdir") or "")
    return f"{target}:{workdir}" if target and workdir else target


def _register_methods() -> None:
    register(Kind(
        name="ssh", label="SSH host", label_zh="SSH 主机",
        settings={
            "type": "object",
            "description": "Uses your ssh config and keys; password prompts are not supported.",
            "x-zh": {"description": "使用你的 ssh 配置和密钥；不支持输入密码。"},
            "properties": {
                "target": {"type": "string", "title": "SSH target", "pattern": r"^[^-\s]\S*$",
                           "description": "user@host, or a host from ~/.ssh/config",
                           "x-zh": {"title": "SSH 目标", "description": "user@host，或 ~/.ssh/config 里的主机"},
                           "examples": ["me@devbox"]},
                "workdir": {"type": "string", "title": "Remote folder", "pattern": r"^[/~]",
                            "description": "The absolute remote folder where choosing a project folder starts",
                            "x-zh": {"title": "远程文件夹", "description": "在这台机器上选择项目文件夹时的起始绝对路径"},
                            "examples": ["/home/me/work"]},
            },
            "required": ["target"],
        },
        check=_check_ssh, admit=_admit_ssh, where=_ssh_where))
    register(Kind(
        name="docker", label="Docker container", label_zh="Docker 容器",
        settings={
            "type": "object",
            "description": "Each conversation gets its own container; the conversation's folders are mounted "
                           "at the same paths.",
            "x-zh": {"description": "每个对话有自己的容器；对话的文件夹挂载在相同路径。"},
            "properties": {
                "image": {"type": "string", "title": "Image", "pattern": r"^[^-\s]\S*$",
                          "description": "The Docker image each conversation's container starts from",
                          "x-zh": {"title": "镜像", "description": "每个对话的容器所用的 Docker 镜像"},
                          "default": DEFAULT_DOCKER_IMAGE, "examples": [DEFAULT_DOCKER_IMAGE]},
            },
        },
        check=_check_docker, normalize=_docker_settings,
        where=lambda settings: str(settings.get("image") or DEFAULT_DOCKER_IMAGE)))


_register_methods()
