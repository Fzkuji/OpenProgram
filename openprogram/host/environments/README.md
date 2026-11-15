# `openprogram/host/environments/`

> Execution environments: where a session's commands and files live.

## Overview

An environment is a named place a conversation runs in. ``local`` is
built in and always present: commands run on this machine and file
tools act on its disk. Users add more in ``config.json``::

    "environments": {
      "default": "local",
      "items": [
        {"id": "devbox", "name": "devbox", "kind": "ssh",
         "ssh_target": "me@devbox", "workdir": "/home/me/work"},
        {"id": "ubuntu", "name": "Ubuntu", "kind": "docker",
         "image": "ubuntu:24.04"}
      ]
    }

A session picks its environment once, when it is created, and keeps it
(``environment_id`` on the session meta). Channels are a separate axis:
a channel decides where a conversation's messages are mirrored
(WeChat, Telegram, ...), never where its tools execute.

Kinds:

* ``local`` — host shell and host filesystem.
* ``ssh`` — commands run over SSH; file tools read and write the remote
  disk through the same connection. ``workdir`` is the remote folder a
  session starts in.
* ``docker`` — one long-lived container per session. The session's
  project folder and extra working folders are bind-mounted at their
  host paths, so file tools keep working on the host while commands run
  inside the container.

The legacy ``backend.terminal`` setting (one global backend) is read as
an environment named after its kind and made the default, so an
existing ``ssh``/``docker`` setup keeps behaving the same.

## Files in this directory

- **`check.py`** — Reachability check for an environment
- **`remote.py`** — Remote filesystem access for SSH environments

_Auto-generated from `__init__.py` docstring — keep that as the source of truth; re-run `python scripts/gen_dir_readmes.py` from the repo root to refresh._
