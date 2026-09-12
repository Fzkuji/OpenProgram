# Read and edit Memory

Open **History → Memory**, then select a topic. Documents and Core source
records open in Preview. Choose **Edit** to change Markdown text. There are no
Save, Saved, or Changes controls.

Each edit is immediately retained in this browser's local draft storage and
queued for automatic file persistence. Writes are serialized so an earlier
response cannot overwrite newer typing. Use Cmd+Z or Ctrl+Z to undo, and
Cmd+Shift+Z, Ctrl+Shift+Z, or Ctrl+Y to redo; automatic saving does not clear
this document's undo history while the app stays open.

File persistence and Git history are separate. Manual edits are grouped into
five-minute checkpoints by the server, with a scheduling tolerance of 15 seconds.
The deadline survives server restart and does not depend on keeping the editor
open. Existing background Memory operations may also create their own commits.
The state before a manual editing interval is retained.

**History** lists revisions from newest to oldest with local date and time.
Select one to inspect the added and removed lines. **Restore this version**
restores that document, first recording the current state, then recording the
restoration as a new commit. Existing history is retained, so restoration can
itself be reversed by selecting another version. Restoration preserves valid
source references and refuses to break links from other Memory documents.

Rejected or conflicting writes retain the draft and show the error. **Review
latest** compares the latest saved text with the draft inside History;
**Replace latest with this draft** explicitly retries against that reviewed
version. A further concurrent change is still rejected.

Source footnotes open a read-only source view. If the local original session
was deleted, the view says **Original session deleted** and does not expose its
old source text. No extra source copy is created when deleting a session.
Already-extracted memory remains. This source-availability rule does not rewrite
the existing Memory Git repository. Archiving hides a session, preserves its
content and links, and does not automatically delete it after an age or capacity
threshold.
