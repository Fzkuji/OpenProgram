# Disabled credential filesystem hardening

`legacy.py` preserves the previous filesystem-hardening implementation for
reference. Production code must not import this directory.

The following policies are permanently disabled unless the user explicitly
requests a separate redesign:

- enforcing file mode `0600` or directory mode `0700`;
- rejecting symlinks, non-regular files, or files owned by another user;
- `O_NOFOLLOW`, inode identity checks, filesystem audit, and permission repair;
- revision-conflict enforcement.

The active package in the parent directory performs ordinary file I/O. It
retains atomic replacement and a basic cross-process write lock only to avoid
partial and lost writes; those mechanisms do not inspect permissions, owners,
symlinks, inodes, or revisions.
