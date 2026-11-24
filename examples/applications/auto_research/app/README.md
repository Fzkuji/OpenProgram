# Auto Research app

An OpenProgram application over the auto_research tasks. Each task of a paper is a
page with its own conversation: the author says what they want, the agent does it in
the paper's folder, and what it makes shows on the page above the conversation.

1. **Welcome page.** Until a project is open, the page lists recent projects with
   their branch and last use. "打开文件夹…" (⌘O) opens the system folder chooser;
   where that cannot open, the page's own folder browser takes over. The page can
   also pick one of OpenProgram's projects. A folder that is not a Git repository
   gets a notice in place, with a button to pick another.
2. **Overview.** Opening a project lands here. The sidebar lists the tasks of
   `auto_research/registry.py` by phase, each with its state: passed, has
   problems, blocked or never run. The tasks still open sit as chips under the
   title. Below them are the paper's PDF and the files changed since the last
   commit, then the project's own conversation.
3. **Task page.** On top: the task, its state, "运行检查" (the task's check, with
   its inputs behind the settings button) and, when the last check failed, what
   it found. In the middle: the task's files, large, one at a time: its own files
   under `workflow/`, the paper's PDF, what its last check wrote, and the files the
   conversation changed. Images, PDFs, Markdown, tables and HTML are drawn; other
   text is shown as text. Under them: the conversation's last exchange, with the
   steps of a reply that is running. At the bottom: OpenProgram's own composer.

The conversation is an ordinary OpenProgram conversation (capability `chat`): one
per project and task, working in the project's folder, listed in the history like
any other and openable in the chat page from the button beside the title. The page
draws its messages; the host draws the composer over the place the page leaves for
it, so the model, permissions, attachments and voice input are the chat's own. With
each message the page sends a note the agent reads beside it: the task, its files,
when it is done, its state and what the last check found (`taskNote` in
`web/src/TaskPage.tsx`). The agent changes the project's files directly; Git
holds the history and the chat's rewind undoes a turn. It never writes
`Approved: yes`: approval stays the author's.

When a reply ends, the page reads the project again, so the file strip shows
what changed. The author runs the check when they want the task judged.

## Who does what

| Part | Done by |
| --- | --- |
| Listing a task's files, reading them, rendering PDF pages | Code (`backend.py`) |
| The work of a task | The author's own agent, in the task's conversation |
| What the agent is told about the task | The page (`taskNote`, `projectNote`), with each message |
| Judging the task | The task's own workflow (paper_revision_check, citation_check, paper_figures) or gates |

## Operations

| Operation | Agent-visible | Input | Result |
| --- | --- | --- | --- |
| `projects` | yes | none | OpenProgram's projects that are Git repositories, the bound folder, the home folder |
| `browse` | no | `path` | the subfolders of one folder, Git repositories marked |
| `pick_folder` | no | `start` | the folder chosen in the macOS folder chooser, or `cancelled` |
| `overview` | yes | none | tasks with state and last runs, sections, phases, the paper's PDF, Git head |
| `run_task` | yes | `task`, `inputs` | the auto_research result |
| `artifacts` | no | `task` (none for the project) | files in groups: changed, paper, deliverables, outputs |
| `pages` | no | `path`, `start`, `count`, `zoom` | JPEG page images |
| `read_text` | yes | `path` | file text |
| `read_file` | no | `path` | an image, base64 |

Live progress of a check comes from `auto_research.trail.listen`: every stage any
trail records, in any workflow, reaches the page as it happens.

## Install, update, develop

The package lives in `~/.openprogram/programs/auto_research`, its own Git
repository; only the commit `openprogram programs check` records there loads. A
copy of that commit ships in OpenProgram as `examples/applications/auto_research`.
To install it from an OpenProgram checkout:

```bash
cp -R examples/applications/auto_research ~/.openprogram/programs/auto_research
git -C ~/.openprogram/programs/auto_research init -q
openprogram programs check ~/.openprogram/programs/auto_research
openprogram programs install ~/.openprogram/programs/auto_research/app --trust
```

The app is project-scoped, but the page picks its own folder and remembers the
recent ones in app storage. After editing the package, run `programs check` again;
after editing `backend.py` or the page, also run the install command with
`--replace`.

The page is built from `web/src/` (React) with OpenProgram's own UI kit: the
shadcn radix-nova components of the web app (`Button`, `Badge`, `SimpleSelect`,
`ToggleGroup`, `Popover`, `Dialog`, `DropdownMenu` with the menu row styles,
`HoverTip`, the toast host), its theme tokens and its light and dark themes. The
build bundles them into static files in `ui/`, so the page loads no remote
script:

```bash
OPENPROGRAM_WEB=<OpenProgram checkout>/apps/web node web/build.mjs
```

The page follows the system's light or dark mode. Rebuild after changing
`web/src/`, then reinstall.

`dev_server.py` serves the same UI outside OpenProgram, with the backend operations
behind the same `window.openprogramApp` calls, for checking the UI in a browser. It
has no conversation: the composer exists only inside OpenProgram.

```bash
python dev_server.py <project_dir> 18190
```

Tests are in `../tests/test_app.py`. The agreed flow and screen mock-ups are in `design/`.

## Notes

- The worker starts from launchd with a short PATH. The backend adds the TeX and
  Homebrew folders so the paper check can build PDFs.
