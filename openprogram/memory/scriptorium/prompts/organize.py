"""Rearranging topic files that already hold the facts."""

ORGANIZE_MEMORY = """Organize the topic files listed below.

This pass is about where things sit, not what they say. Add or merge a heading, move a paragraph under the heading it belongs to, split a file that now covers two subjects, repair a link. Leave the prose and the footnote definitions exactly as they are — you are not rewriting them, and a paragraph already in a sensible place needs nothing.

Make each change with a small Edit. Never rewrite a whole file: these files are mostly footnote definitions, and reproducing them costs far more than the organizing is worth. If a file is already well organized, say so and move on. Doing nothing is a valid outcome.

Every block ID that exists now must still exist when you finish, on the paragraph it belongs to. Moving a paragraph carries its ID along. Splitting a paragraph keeps the original ID on one part; the other part gets no ID and the Runtime assigns one. Merging paragraphs keeps every ID involved, all of them on the resulting paragraph, separated by spaces. An ID that disappears breaks every view and link that reaches it.

Topic files:
{topic_paths}"""
