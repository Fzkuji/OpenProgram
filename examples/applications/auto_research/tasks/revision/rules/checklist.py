"""Every item of the AML writing guide, the venue rules and the author's own requirements,
in the order a revision is checked.

The phases follow the work: first the process gates, then the integrity red lines, then
what each part of the paper must say, then how sentences are worded, then figures and
tables, LaTeX form, and last the layout and submission items. A problem found early
makes the later phases moot, so the report prints the phases in this order.

Each item says how it is checked:
  auto    - deterministic checks; the item takes the worst level of the named checks.
  review  - the reviewer model reads the text against the named writing rules.
  manual  - needs an outside tool or a person; the report lists it as TODO.
  built-in - met by how this workflow runs; the reason is shown.
  n/a     - does not apply to this camera-ready; the reason is shown.
Nothing is dropped: the report prints one line per item.
"""

AML = "AML guide"
CHAT = "Author's requirements"
IAB = "IAB camera-ready email"
GUIDES = "Writing guides"

PHASES = [
    ("1. Process: plan, draft, check, write in", [
        (CHAT, "-", "Plan card (purpose, evidence, limits) approved before a section is written", "built-in",
         "auto_research blocks paper_check of a section without an approved card; the reviewer reads each paragraph against it."),
        (CHAT, "-", "Plans and claims are shown to the author in Chinese", "built-in",
         "auto_research's next-step instructions require it."),
        (AML, "2c", "Outline approved before writing", "n/a", "Camera-ready of an accepted paper."),
        (AML, "2b", "Read this guide", "built-in", "This checklist holds every item of the guide."),
        (AML, "3a1", "Check against this guide before showing new content", "built-in", "This run is that check."),
        (CHAT, "-", "Changes are made in a draft copy and shown as a red/green PDF", "auto", ("diff",)),
        (AML, "3b2", "Highlight large changed passages", "auto", ("diff",)),
        (CHAT, "-", "Fixes that are checked and correct are written in and committed without asking", "built-in",
         "Author's rule of 2026-10-31; run.json records every stage of the check."),
        (AML, "1i", "Back up the LaTeX sources", "built-in", "The paper is a Git repository; the workflow refuses anything else."),
        (AML, "1j", "Name versions by time", "built-in", "Each check is saved under drafts/checks/<timestamp>/."),
        (AML, "2a", "Project named Year-Venue-Name-Title; one numbered .tex file per section", "auto", ("file-layout",)),
        (AML, "3b1", "Record every supervisor comment and mark it done", "manual",
         "Keep the comment list in the outline document."),
    ]),
    ("2. Integrity: sources, numbers, references", [
        (AML, "0", "No plagiarism, including figures and self-plagiarism (six identical words in a row)",
         "manual", "Upload draft.pdf to iThenticate and send the report to the supervisor."),
        (AML, "0", "No fabricated data or results; every number has a source", "auto", ("numbers",)),
        (AML, "0", "No fabricated or unverified references", "auto", ("cite-keys", "bib")),
        (AML, "0", "Every reference verified on Google Scholar, RefCheck or CiteTrue",
         "manual", "The auto_research citation_check task checks the .bib files; the author confirms flagged entries."),
        (AML, "0", "Edits to the .bib file are verified against the published record (the check lists them)",
         "auto", ("bib",)),
        (AML, "6b", "Bib entries from Google Scholar only", "manual", "Confirmed by the author when adding entries."),
        (AML, "6b", "Cite the published version, not arXiv; no duplicate versions", "auto", ("bib-arxiv", "bib-duplicate")),
        (AML, "0", "AI-generated text check", "manual", "Run GPTZero, Binoculars or Fast-Detect-GPT on the new text."),
    ]),
    ("3. Structure: what each part must say", [
        (AML, "5", "Introduction follows background, problem, gap, approach, contributions", "review", (14,)),
        (AML, "6b", "Related work subsections end with shortcomings and our advantage", "review", (15,)),
        (AML, "4", "Each section gives motivation (why) before method (how)", "review", (13,)),
        (CHAT, "-", "Method subsections open with the challenge and its motivation, citing one or two works", "review", (34,)),
        (CHAT, "-", "Each result paragraph runs why, setup, finding, meaning, limits; figures cited in passing",
         "review", (33,)),
        (AML, "10a", "Settings: data, metrics, implementation details, baselines", "review", (16,)),
        (AML, "10b", "Overall performance: phenomenon, reason, conclusion for each point", "review", (4,)),
        (CHAT, "-", "Results discuss results; one figure per paragraph; no filler", "review", (5, 7, 8, 11)),
        (AML, "10b", "Results agree with the overall performance numbers everywhere", "review", (20,)),
        (AML, "10b", "Significance test against the best baseline", "n/a", "Competition ranking; no baseline comparison."),
        (AML, "4", "Every claim is supported by citations or our experiments", "review", (6,)),
        (CHAT, "-", "The text follows the approved plan card of its section", "review", (31,)),
        (CHAT, "-", "No groupings, framing or qualifiers the evidence does not contain", "review", (32,)),
        (CHAT, "-", "The paper's own writing rules (writing_rules in workflow/rules.json)", "review", (35,)),
        (AML, "7b", "Few symbols, each explained, one meaning each", "review", (17,)),
        (AML, "3a5", "Polished text keeps the meaning, terms and LaTeX commands", "review", (2, 9)),
    ]),
    ("4. Wording: the author's sentence rules", [
        (CHAT, "-", "Do not open with \"Table X reports\" or \"Figure X shows\"", "auto", ("opening",)),
        (CHAT, "-", "Point first; plain words; short sentences", "review", (1, 2, 3)),
        (CHAT, "-", "Short sentences; at most two commas", "auto", ("commas",)),
        (CHAT, "-", "No whether, although, despite, rather than, instead of", "auto", ("wording", "opener")),
        (CHAT, "-", "No contrasts, colons or dashes to explain", "review", (12,)),
        (CHAT, "-", "Say what the method does, not what it is not (\"we do not use X\")", "auto", ("negation",)),
        (CHAT, "-", "Rejected terms: branches, 359, 99%, version; preferred: iteration, base model", "auto",
         ("term", "term-preferred")),
        (CHAT, "-", "No SOTA", "auto", ("sota",)),
        (AML, "4", "No absolute wording (obvious, always, never, avoid, eliminate)", "auto", ("absolute",)),
        (CHAT, "-", "Bold only key words", "auto", ("bold",)),
        (CHAT, "-", "At most four \\paragraph headings per section", "auto", ("paragraph-count",)),
    ]),
    ("5. Expression: public writing guides", [
        (GUIDES, "-", "No words that models overuse (delve, pivotal, showcase, crucial, ...)", "auto", ("ai-words",)),
        (GUIDES, "-", "No significance hype (paves the way, sheds light on, bridges the gap)", "auto", ("hype",)),
        (GUIDES, "-", "No empty intensifiers or vague hedges (very, extensive experiments, somewhat)", "auto", ("intensifier",)),
        (GUIDES, "-", "No boilerplate emphasis or filler (it is worth noting, in order to)", "auto", ("boilerplate",)),
        (GUIDES, "-", "Plain copulas: is, are, has instead of serves as, stands as", "auto", ("copula",)),
        (GUIDES, "-", "Action in the verb, not in a nominalization", "auto", ("nominalization",)),
        (GUIDES, "-", "Verbs no stronger than the evidence (prove, guarantee, significantly)", "auto", ("overclaim",)),
        (GUIDES, "-", "No generic opening (In recent years, With the rapid development)", "auto", ("formulaic-opener",)),
        (GUIDES, "-", "\"This\" followed by a noun", "auto", ("vague-this",)),
        (GUIDES, "-", "Sentences of at most 30 words", "auto", ("long-sentence",)),
        (GUIDES, "-", "No chains of Moreover/Furthermore; no three sentences with the same opening", "auto",
         ("connectives", "repeated-opener")),
        (GUIDES, "-", "Subject near verb; known context first, new information last", "review", (21,)),
        (GUIDES, "-", "Action in verbs; one term per concept", "review", (22, 23)),
        (GUIDES, "-", "Claim strength matches evidence; calibrated hedges only", "review", (24, 30)),
        (GUIDES, "-", "Sentences connect; paragraphs support the section claim", "review", (25,)),
        (GUIDES, "-", "Specific opening sentences and contributions", "review", (26, 27)),
        (GUIDES, "-", "Bold labels state the point; captions stand alone", "review", (28, 29)),
    ]),
    ("6. Figures and tables", [
        (CHAT, "-", "Original figures and content are not deleted", "auto", ("removed", "deleted")),
        (CHAT, "-", "Figures and tables at the top of the page", "auto", ("placement",)),
        (AML, "8a", "Every figure is referred to in the text", "auto", ("mention",)),
        (AML, "8b", "Figure on the same page as its first mention", "auto", ("same-page",)),
        (AML, "8c", "Figures in order of first mention", "auto", ("order",)),
        (AML, "8d", "Vector figures (PDF), no PNG or JPG", "auto", ("vector",)),
        (AML, "8e", "Text in figures at least caption size", "auto", ("figure-font",)),
        (AML, "8h", "No light or bright colors", "manual", "Look at every figure in draft.pdf."),
        (AML, "8i", "Symbols in figures match the text", "manual", "Look at every figure in draft.pdf."),
        (AML, "8j", "No Type 3 fonts", "auto", ("type3",)),
        (CHAT, "-", "Figures in their natural form: no bar chart where a table exists, continuous iteration data "
                    "not binned, subfigure letters via LaTeX, legend markers of one size", "manual",
         "Look at every figure in draft.pdf."),
        (AML, "9a", "Every table is referred to in the text", "auto", ("mention",)),
        (AML, "9b", "Table on the same page as its first mention", "auto", ("same-page",)),
        (AML, "9c", "Tables in order of first mention", "auto", ("order",)),
        (AML, "9d", "Booktabs style, no vertical lines", "auto", ("booktabs",)),
        (CHAT, "-", "No custom font size inside tables", "auto", ("table-font",)),
        (CHAT, "-", "All models in one table; no model names or marks after method names; numeric columns "
                    "centered; headers capitalized and bold", "manual", "Look at every table in draft.pdf."),
        (CHAT, "-", "Table caption above, figure caption below", "auto", ("caption-position",)),
        (AML, "4", "Captions in sentence case ending with a period", "auto", ("caption",)),
        (CHAT, "-", "Captions carry no numbers, model names or pointers to other parts", "auto",
         ("caption-numbers", "caption-model", "caption-content")),
    ]),
    ("7. LaTeX form", [
        (AML, "4", "Space or ~ after (), [] and {}", "auto", ("spacing",)),
        (AML, "4", "Quotes written as `` and ''", "auto", ("quotes",)),
        (AML, "4", "e.g., and i.e., end with a comma", "auto", ("eg-ie",)),
        (AML, "4", "Subsection titles capitalize main words", "auto", ("heading-case",)),
        (AML, "4", "itemize uses [leftmargin=*]", "auto", ("itemize",)),
        (AML, "6a", "References written Section~\\ref, Figure~\\ref, ... with capitals", "auto",
         ("ref-form", "label-prefix")),
        (AML, "6b", "Text~\\cite{} with a space before the citation", "auto", ("cite-space",)),
        (AML, "6b", "Use \\citep or \\citet", "auto", ("cite-form",)),
        (AML, "6b", "A citation is never the subject of a sentence", "auto", ("cite-subject",)),
        (AML, "7a", "Inline math uses $...$, not \\(...\\)", "auto", ("math-delim",)),
        (AML, "7a", "Display equations end without comma or period", "auto", ("display-punct",)),
        (AML, "7b", "Bold with \\boldsymbol", "auto", ("boldsymbol",)),
        (AML, "3a3", "\\zxy and \\zc comment commands defined; none left in the text", "auto",
         ("comment-macros", "placeholder")),
        (CHAT, "-", "No placeholders, TBD, red notes or guessed content", "auto", ("placeholder",)),
        (AML, "4", "Last line of every paragraph, list item and caption has at least four words", "auto", ("last-line",)),
        (CHAT, "-", "Last line of paragraphs, list items and captions longer than half a line", "auto", ("last-line",)),
        (CHAT, "-", "No overfull lines or broken references", "auto", ("overfull", "references")),
    ]),
    ("8. Layout and submission", [
        (AML, "1c", "Page limit: main text, references and appendix", "auto", ("pages", "build", "appendix-build")),
        (IAB, "-", "Main text at most five pages", "auto", ("pages",)),
        (AML, "1d", "Meta information matches the venue", "auto", ("format",)),
        (IAB, "-", "Camera-ready style, workshop title, no line numbers", "auto", ("format",)),
        (IAB, "-", "Agent Behavior Analysis section kept", "auto", ("format",)),
        (IAB, "-", "All authors and affiliations", "auto", ("authors",)),
        (AML, "1f", "Anonymity; no \"our previous work\"", "auto", ("authors", "previous-work")),
        (AML, "1g", "Code is shared", "auto", ("code-link",)),
        (AML, "1e", "Title and abstract match the submission system",
         "manual", "Compare with OpenReview forum o2hcUEF9mV before upload."),
        (AML, "1b", "Grammarly and a full manual pass (logic, consistency, language, figures)",
         "manual", "Run Grammarly on the changed paragraphs; read draft.pdf once."),
        (AML, "3a2", "Grammarly on new content", "manual", "Run Grammarly on the added paragraphs in report.md."),
        (AML, "1h", "Upload a version one day early", "manual", "Upload to OpenReview before Oct 31 AoE."),
        (AML, "1k", "Watch the venue site and email after submission", "manual", "The author watches the IAB emails."),
        (AML, "1a", "Register similar papers at different times", "n/a", "Camera-ready; already registered."),
        (AML, "6b", "Few self-citations at submission", "n/a", "Camera-ready."),
        (AML, "6b", "Cite well-known researchers in the area", "manual", "The author decides when adding references."),
    ]),
]

# One row per item: (phase, source, number, requirement, how, detail), in checking order.
CHECKLIST = [(phase, *row) for phase, rows in PHASES for row in rows]

# A phase is named by its number: "4" is "4. Wording: the author's sentence rules".
PHASE_IDS = {name.split(".", 1)[0]: name for name, _ in PHASES}


def select(phases: str = "") -> list[tuple]:
    """The checklist rows of the chosen phases ("3,4" or empty for all), in checking order."""
    chosen = [p.strip() for p in phases.split(",") if p.strip()]
    unknown = [p for p in chosen if p not in PHASE_IDS]
    if unknown:
        raise ValueError(f"unknown phase {', '.join(unknown)}; phases are " +
                         "; ".join(PHASE_IDS.values()))
    names = {PHASE_IDS[p] for p in chosen} or set(PHASE_IDS.values())
    return [row for row in CHECKLIST if row[0] in names]


def checks_of(rows) -> set[str]:
    """The deterministic check names the rows read."""
    return {name for row in rows if row[4] == "auto" for name in row[5]}


def rules_of(rows) -> set[int]:
    """The writing rule numbers the rows send to the reviewer."""
    return {n for row in rows if row[4] == "review" for n in row[5]}
