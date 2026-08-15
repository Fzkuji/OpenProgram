"""Rearranging topic files that already hold the facts."""

ORGANIZE_MEMORY = """Organize the topic files listed below.

Review both the organization and the current usefulness of the Topic memory. Add or merge a heading, move a paragraph under the heading it belongs to, split a file that covers two subjects, repair a link, combine redundant material, or delete memory that should no longer be active. Git records the prior state.

Make each change with a small Edit. Never rewrite a whole file: these files are mostly footnote definitions, and reproducing them costs far more than the organizing is worth. If a file is already well organized, say so and move on. Doing nothing is a valid outcome.

If a paragraph remains, keep its existing block ID. Moving a paragraph carries its ID along. Splitting a paragraph keeps the original ID on one part; the other part gets no ID and the Runtime assigns one. When deleting a block, remove or update every link to that block in the same turn.

Topic files:
{topic_paths}"""
