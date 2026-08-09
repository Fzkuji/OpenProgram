"""Standing instructions for retrieving evidence from the workspace."""

RETRIEVAL_PROMPT = """Answer one memory-benchmark question from a read-only memory workspace.
Condition: {condition}
Bash working directory: {workspace_root}
The shell starts in this directory. Use Inventory paths relative to this directory.
Do not invent or prepend another workspace path. Do not use absolute paths,
parent-directory paths, shell control operators, or redirections.

The workspace has no fixed directory taxonomy. Use the general read-only bash
tool or the specialized memory tools and the actual inventory below. Search
wording may differ from the question, so inspect semantically relevant files
and use several literal queries when needed.

When useful, first locate likely files, inspect their headings, and read only
the relevant sections. This is a recommendation, not a required tool order.

For temporal, update, counting, comparison, and multi-session questions,
inspect all relevant events. Preserve historical states; prefer the latest fact
only when the question asks for current state.
{source_verification_guidance}

When the question states an explicit calendar window, or a calendar window has
already been resolved from evidence, pass optional date_from/date_to in the same
BM25 or embedding tool call. Each value may be YYYY, YYYY-MM, or YYYY-MM-DD.
Omit hard date filters for ambiguous relative or event-based time expressions
until their calendar bounds have been established.

Use non-empty evidence from the initial Core/Recent Memory or from retrieval
tools before answering. Do not answer from the inventory, file names, prior
knowledge, or assumptions. If the recorded history does not contain the
requested fact, state that directly.

Answer briefly and directly, using exact words from the evidence wherever
possible. Do not pad the answer with description the question did not ask for.

When the workspace holds nothing about the subject asked about, say that no
such information is recorded. Facts about a neighbouring subject are not
partial evidence for this one. Where the evidence does hold the values needed
to work the answer out, compute it rather than declining.

Core Memory:
{core_memory}

Recent Memory:
{recent_memory}

Inventory:
{inventory}

Current Date: {question_date}
Question: {question}

After tool use, output exactly one <answer>...</answer> block and no reasoning.
"""
