"""Sealed versions: the on-disk store a managed install runs from.

Each version is a complete, sealed directory under ``<state>/runtime/versions``.
A one-file pointer names the current one, and a small launcher reads it,
checks the files and starts Python. ``layout`` names the paths, ``pointer``
moves the pointer atomically, ``store`` seals and reads versions, ``retention``
deletes what is no longer kept, and ``build`` makes a new version from a commit.
"""
