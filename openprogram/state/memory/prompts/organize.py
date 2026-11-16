"""Rearranging topic files that already hold the facts."""

from ..workspace_layout import FLAT_RUN_LIMIT

ORGANIZE_MEMORY = """Organize the topic files listed below.

Review both the organization and the current usefulness of the Topic memory. Add or merge a heading, move a paragraph under the heading it belongs to, split a file that covers two subjects, repair a link, combine redundant material, or delete memory that should no longer be active. Remove repeated status checks, unchanged measurements, transient tool failures, and obsolete project progress when they no longer help a future interaction. Git records the prior state.

When `topics/core.md` is listed, keep only preferences, identity facts, long-term goals, and mandatory rules that are useful across most future interactions. Move project-specific material out of `topics/core.md` into its project Topic when it remains useful; delete it when the current Topic state or project files already provide the answer. A paragraph being outside the rendered token budget is not a reason to retain it in the Core master.

Fix every filing problem the workspace structure lists, with the Move tool: give a thing with several notes its own folder, put folders of one kind under a folder named for the kind, and make a note beside a same-named folder that folder's overview.md. A move changes no byte of a file, and the Runtime rewrites the links and the `MEMORY.md` paths that pointed at the old place, so regrouping never needs a file rewritten.

Split every file the workspace structure marks as over the limit into the directory named beside it: an overview.md for what applies to the whole subject, and one file per part, grouped into deeper directories where the parts have parts. Write each new file in one call with its paragraphs, their trailing IDs and their footnote definitions copied exactly, then remove the original file with the shell. Merge a part file back into its parent when it holds only a paragraph or two.

Apart from splitting and moving, make each change with a small Edit. Never rewrite a whole file just to rearrange it: these files are mostly footnote definitions, and reproducing them costs far more than the organizing is worth. If a file is already well organized, say so and move on. Doing nothing is a valid outcome.

Finish by bringing `MEMORY.md` up to date with what the tree now is. Read it, then fix what is stale: files added, moved, split or removed; a subject described by a bare title or a quoted fact instead of by what it covers (a generated map names each file by its title — replace each with a short phrase such as "visa requirements and application materials"); and what is currently active. Rewrite it in one Write when most of it changes, otherwise Edit the lines that did. Leave it alone when it is already right.

If a paragraph remains, keep its existing block ID. Moving a paragraph carries its ID along. Splitting a paragraph keeps the original ID on one part; the other part gets no ID and the Runtime assigns one. When deleting a block, remove or update every link to that block in the same turn.

Topic files:
{topic_paths}"""

REGROUP_MEMORY = """Regroup the notes that sit directly in `{directory}/`. It holds {count} of them, and no directory holds more than {limit}.

Read the titles and the paragraph summaries in the workspace structure, and Read a note only when those do not say what it is about. Decide, for each note, which real-world thing it is about — a company, a person, a paper, a trip, a project — and what kind of thing that is. Then file them with one Move call:

- Things of one kind go under a folder named for the kind, inside `{directory}/`, each thing in a folder named after it alone. A thing's only note becomes its `overview.md`; where a thing has several notes, the one about the whole of it is `overview.md` and the others are named by their aspect.
- A note already split into a folder beside it joins that folder: the thing's folder holds all of its notes, and the old folder name does not survive next to the new one.
- Where the notes themselves treat some of these things as a group, the group is a folder inside the kind folder, and a note about the whole group is its `overview.md`.
- A note about no single thing — material shared by all of them, a comparison across several — stays in `{directory}/` or becomes the `overview.md` of the folder it describes.
- A file name never repeats what its folders already say, and never carries a date.

Move files marked read-only like any other; do not edit them. Do not rewrite, merge or delete anything in this pass: only Move. The Runtime rewrites links and the paths in `MEMORY.md`.

Finish by bringing `MEMORY.md` in line with the new tree: its paths are already right, so regroup its lines under the folders the notes now sit in, with one line for a kind folder saying what it holds rather than a line per file where that reads better. Keep it under 6 KB.

Notes directly in `{directory}/`:
{topic_paths}""".replace("{limit}", str(FLAT_RUN_LIMIT))
