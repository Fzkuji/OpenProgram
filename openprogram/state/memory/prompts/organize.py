"""Rearranging topic files that already hold the facts."""

ORGANIZE_MEMORY = """Organize the topic files listed below.

Review both the organization and the current usefulness of the Topic memory. Add or merge a heading, move a paragraph under the heading it belongs to, split a file that covers two subjects, repair a link, combine redundant material, or delete memory that should no longer be active. Remove repeated status checks, unchanged measurements, transient tool failures, and obsolete project progress when they no longer help a future interaction. Git records the prior state.

When `topics/core.md` is listed, keep only preferences, identity facts, long-term goals, and mandatory rules that are useful across most future interactions. Move project-specific material out of `topics/core.md` into its project Topic when it remains useful; delete it when the current Topic state or project files already provide the answer. A paragraph being outside the rendered token budget is not a reason to retain it in the Core master.

Split every file the workspace structure marks as over the limit into the directory named beside it: an overview.md for what applies to the whole subject, and one file per part, grouped into deeper directories where the parts have parts. Write each new file in one call with its paragraphs, their trailing IDs and their footnote definitions copied exactly, then remove the original file with the shell. Merge a part file back into its parent when it holds only a paragraph or two.

Apart from splitting, make each change with a small Edit. Never rewrite a whole file just to rearrange it: these files are mostly footnote definitions, and reproducing them costs far more than the organizing is worth. If a file is already well organized, say so and move on. Doing nothing is a valid outcome.

Finish by bringing `MEMORY.md` up to date with what the tree now is. Read it, then fix what is stale: files added, moved, split or removed; a subject described by a bare title or a quoted fact instead of by what it covers (a generated map names each file by its title — replace each with a short phrase such as "visa requirements and application materials"); and what is currently active. Rewrite it in one Write when most of it changes, otherwise Edit the lines that did. Leave it alone when it is already right.

If a paragraph remains, keep its existing block ID. Moving a paragraph carries its ID along. Splitting a paragraph keeps the original ID on one part; the other part gets no ID and the Runtime assigns one. When deleting a block, remove or update every link to that block in the same turn.

Topic files:
{topic_paths}"""
