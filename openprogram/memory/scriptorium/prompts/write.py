"""Recording new facts from source text."""

WRITE_MEMORY = """Integrate the conversation below into the memory workspace.

Every fact you record goes into a subject file under `topics/`, created or revised with the Write and Edit tools. Follow the Topic block and evidence-footnote contract in the system prompt.

Decide which subject each fact belongs to and write it there: a person, a relationship, a recurring theme. Where a fact was said is not where it is stored, so `topics/people/calvin.md` is right and a file named after a conversation or a date is wrong.

The complete source text is below and nothing needs to be copied anywhere. Files under `sources/` are read-only evidence and will refuse to be written; if an edit there fails, that is the workspace working as intended, and the fix is to write the fact into `topics/` instead. Cover everything supplied before finishing.

Preserve complete historical state changes. Use an observation date only to resolve explicit relative dates in the text it heads, not as the default date of every fact.

Update `core.md` only for stable information that should be visible in every future interaction, such as persistent preferences, long-term goals, active ongoing work, or mandatory constraints. Keep source references in Core Memory.

{sessions}"""
