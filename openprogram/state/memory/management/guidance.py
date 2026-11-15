"""What a memory writer should change, for the check failures that recur.

The raw message names the rule that was broken; a writer that has just
broken it needs the correction instead. Shared by the per-edit check the
Write and Edit tools run and by the repair turn after a rejected commit.
"""
from __future__ import annotations


def repair_guidance(error: str) -> str:
    if "memory source links required" in error or "undefined footnote" in error:
        return (
            "A citation needs its `[^eN]:` definition line in the same file. "
            "Either add the definition or leave the existing `[^e-...]` "
            "citation exactly as it was."
        )
    if "unused footnote definition" in error:
        return (
            "A `[^eN]:` definition needs a `[^eN]` citation in a paragraph of "
            "the same file. Cite it where the fact is, or remove the definition."
        )
    if "invalid source target" in error or "missing source reference" in error:
        return (
            "A footnote cites a chat message by the ref printed beside it, "
            "e.g. `[keyword](/s/<session>#<message>)`. Copy the ref exactly; "
            "an existing `[^e-...]` definition stays as it was."
        )
    if (
        "not evidence of this transaction" in error
        or "outside runtime-selected batch" in error
    ):
        return (
            "A new `[^eN]` footnote may cite only the conversation in this "
            "task, by the refs printed beside its messages. When you move or "
            "rewrite a paragraph that is already in memory, keep its existing "
            "`[^e-...]` citation and definition instead of writing a new one."
        )
    if "written outside OpenProgram" in error:
        return (
            "That file was written outside OpenProgram and is read-only. "
            "Leave it exactly as it was (no edit, move or deletion, and no "
            "link to its paragraphs) and put the facts in a Topic file of "
            "memory's own."
        )
    if (
        "memory block ID required" in error
        or "footnote required" in error
        or "cites no chat message" in error
    ):
        return (
            "Every paragraph in a Topic file must end with an evidence "
            "citation. Bullet lists and uncited prose are not allowed there: "
            "fold them into a cited paragraph or remove them. Headings are fine."
        )
    if "duplicate memory_id" in error:
        return (
            "A paragraph's trailing `^id` may appear once in all of memory. "
            "When you move a paragraph, delete it from where it was."
        )
    if "duplicate footnote definition" in error:
        return (
            "Each footnote label is defined once per file. Number the "
            "footnotes you add `[^e1]`, `[^e2]`, … without reusing a label."
        )
    if "Topic-to-Topic link must target" in error:
        return (
            "A link to another Topic file must end in `#^<block-id>` of a "
            "paragraph you can see there, e.g. `(../people/dave.md#^7ffb575c)`."
        )
    if "block ID must not be removed" in error:
        return (
            "A paragraph already in memory keeps its trailing `^id` wherever "
            "it moves. Put the ID back, or delete the paragraph on purpose."
        )
    return ""
