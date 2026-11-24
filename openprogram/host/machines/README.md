# `openprogram/host/machines/`

> Machines: the computers OpenProgram reaches, and the one a conversation runs on.

## Overview

A machine is a computer with its own operating system: this computer, an
SSH host, a Docker machine, a virtual machine, a phone. It has up to four
connections (``openprogram.host.machines.kinds``), each giving one kind of
target: ``shell`` runs commands and reads files, ``window`` operates
application windows (type ``window``), ``screen`` operates the whole display
(type ``screen``), ``browser`` is a browser OpenProgram runs there
(type ``browser``). A fifth, ``openprogram``, is another computer's own
OpenProgram (``peer``): its conversations are listed beside this computer's
and forwarded there, never copied. One kind may serve several connections; two
connections of one machine with the same kind and address share one client.

This computer is built in as ``local`` and never stored. Its connections
are ``shell: local``, ``window: desktop`` and ``browser: built_in``; it has
a ``screen`` connection (``desktop``) only while ``computer_use.whole_screen``
is on, which it is not by default. Users add more in ``config.json``::

    "machines": {
      "local": {"window": {"kind": "desktop"}, "browser": {"kind": "built_in"}},
      "items": [
        {"id": "devbox", "name": "Dev box",
         "shell": {"kind": "ssh", "target": "me@devbox", "workdir": "/srv/app"}},
        {"id": "ubuntu", "name": "Ubuntu",
         "shell": {"kind": "docker", "image": "ubuntu:24.04"}},
        {"id": "lab", "name": "Lab VM",
         "screen": {"kind": "osworld", "url": "http://192.168.64.2:5000"}}
      ]
    }

Two bindings sit on this one registry and stay apart:

* The **environment** is a role: the machine a conversation's commands and
  file tools run on. It needs a shell. The conversation's project decides
  it: a project is a folder on one machine (``machine_id`` on the project
  record), and every conversation in it runs on that machine, in that
  folder. The home project is always this computer. A project whose machine
  was removed, switched off or has no shell is refused, never replaced by
  this computer.
* The **targets** of ``computer_use`` are listed per turn and chosen
  per call, from every machine's window, screen and browser connections. Being the
  environment never makes a machine the default target.

Channels are a third axis: a channel decides where a conversation's messages
are mirrored, never where its tools execute.

Design: docs/reference/design/integrations/computer-use/machines-and-targets.html

## Files in this directory

- **`check.py`** — Reachability checks: one quick round trip per connection of a machine
- **`here.py`** — Whether an address reaches this computer
- **`kinds.py`** — Connection kinds: how one capability of a machine is reached
- **`peer.py`** — Another OpenProgram: a machine reached through its own Worker
- **`remote.py`** — File access on a machine whose shell kind keeps files there (``Kind.remote_files``)
- **`status.py`** — Connection state of each machine: what the project picker, the sidebar and the composer show beside its icon

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/generators/gen_dir_readmes.py` from the repo root to refresh._
