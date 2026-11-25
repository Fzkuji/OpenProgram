# `openprogram/host/machines/`

> Machines: the devices OpenProgram reaches, and the one a conversation runs on.

## Overview

A device is a computer with its own operating system: this computer, an
SSH host, a Docker machine, a virtual machine, another computer's own
OpenProgram. It is an id, a name, a switch, and exactly one connection:
how it is reached. The connection's kind is its connection method
(``openprogram.host.machines.kinds``): ``local``, ``ssh``, ``docker``,
``openprogram`` or ``osworld``.

The device layer knows nothing about what can be done once connected. Each
feature decides that from the method: commands and files
(``openprogram.host.backend``) run through ``local``, ``ssh`` and
``docker``; ``computer_use`` operates this computer's windows, screen and
browsers through ``local``, a virtual machine's screen through ``osworld``
and another computer through ``openprogram``; another computer's
conversations are listed here and forwarded there through ``openprogram``
(``peer``).

This computer is built in as ``local``, reached by the method ``local``,
and never stored. Users add more in ``config.json``::

    "machines": {
      "items": [
        {"id": "devbox", "name": "Dev box",
         "connection": {"kind": "ssh", "target": "me@devbox", "workdir": "/srv/app"}},
        {"id": "ubuntu", "name": "Ubuntu",
         "connection": {"kind": "docker", "image": "ubuntu:24.04"}},
        {"id": "lab", "name": "Lab VM",
         "connection": {"kind": "osworld", "url": "http://192.168.64.2:5000"}}
      ]
    }

Two bindings sit on this one registry and stay apart:

* The **environment** is a role: the machine a conversation's commands and
  file tools run on. It needs a method the shell feature runs commands
  through. The conversation's project decides it: a project is a folder on
  one machine (``machine_id`` on the project record), and every
  conversation in it runs on that machine, in that folder. The home
  project is always this computer. A project whose machine was removed,
  switched off or cannot run commands is refused, never replaced by this
  computer.
* The **targets** of ``computer_use`` are listed per turn and chosen per
  call, from what ``computer_use`` offers on each device. Being the
  environment never makes a machine the default target.

Channels are a third axis: a channel decides where a conversation's messages
are mirrored, never where its tools execute.

Design: docs/reference/design/integrations/computer-use/machines-and-targets.html

## Files in this directory

- **`check.py`** — Reachability checks: one quick round trip to a machine
- **`here.py`** — Whether an address reaches this computer
- **`kinds.py`** — Connection methods: how a device is reached
- **`peer.py`** — Another OpenProgram: a machine reached through its own Worker
- **`remote.py`** — File access on a machine whose shell keeps files there (``Shell.remote_files`` in ``host.backend``)
- **`status.py`** — Connection state of each machine: what the project picker, the sidebar and the composer show beside its icon

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
