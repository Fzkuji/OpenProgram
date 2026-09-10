# Read and edit Memory

Open **History → Memory**, then select a topic. The document opens in Preview,
with Markdown formatting and source footnotes. Choose **Edit** to change its
source text. Core source records use the same editor; the Core prompt preview
shows the text currently available for injection.

Changes save automatically after an 800 ms typing pause. Leaving the document
also starts a pending save. There is no Save button. Unsaved drafts are retained
in this browser's local storage across navigation and reload. Keep the page open
if local draft storage is unavailable and saving has not finished.

**Changes** compares the current document with the text loaded at the start of
this editing session, including edits already saved automatically. **History**
lists Git revisions from newest to oldest with local date and time. Select one
to see the lines added and removed in that revision. **Load older versions**
retrieves more entries.

Saving validates source references and rebuilds derived Memory views before
recording a Git commit. Invalid edits remain in the editor with the reason they
were rejected. A Git failure is shown separately from a successful file save.

If another writer changes the same file, automatic saving refuses to overwrite
it. Choose **Review latest** to compare the latest saved version with your draft.
**Replace latest with this draft** explicitly retries against the reviewed
version; a further concurrent change is still rejected.
