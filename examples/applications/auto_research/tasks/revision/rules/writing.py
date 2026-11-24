"""Writing rules: the AML guide and the author's review of earlier revisions.

The deterministic checks use the word lists. The reviewer model reads WRITING_RULES;
the checklist refers to these rules by number.
"""

import re

# Terms the author rejected for this paper. Checked only in added text.
BANNED_TERMS = {
    "main branch": "Do not mention branches; the paper counts 725 iterations in total.",
    "main-branch": "Do not mention branches; the paper counts 725 iterations in total.",
    "359": "Use the single total of 725 iterations.",
    "99\\%": "The fixed deployments ran without the LLM judge; do not report a code share.",
    "recorded version": "Use the paper's own term, iteration.",
}

# Terms with a preferred form in this paper. Checked only in added text.
PREFERRED_TERMS = {"backbone": "base model", "commit": "iteration", "commits": "iterations"}

# Sentences that describe the method by what it is not. Checked only in added text.
NEGATIONS = (r"\bwe do not (?:use|rely on|require|need|adopt)\b", r"\bnot (?:\w+ ){1,3}but\b")

# Captions name what is shown; numbers, model names and pointers belong in the text.
CAPTION_MODEL_NAMES = ("GPT", "Claude", "Gemini", "Llama", "Qwen", "DeepSeek", "Mistral",
                       "Sonnet", "Opus", "Haiku", "o1", "o3", "o4")
CAPTION_POINTERS = ("Appendix", "see Section", "see Table", "see Figure", "best viewed", "data from", "taken from")

# Words and openers the author rejected as filler or hedging. Checked only in added text.
DISCOURAGED_WORDS = ("whether", "although", "despite", "notwithstanding", "rather than",
                     "instead of", "on the one hand", "on the other hand")
DISCOURAGED_OPENERS = ("Once", "Although", "Notably", "Interestingly", "Importantly")
MAX_COMMAS_PER_SENTENCE = 2

# AML guide section 4: replace absolute wording.
ABSOLUTE_WORDS = {"obvious": "straightforward", "obviously": "straightforwardly",
                  "always": "generally / usually / often", "never": "rarely",
                  "avoid": "alleviate / relieve", "avoids": "alleviates",
                  "eliminate": "alleviate / relieve", "eliminates": "alleviates"}

# Expression rules from public writing guides (sources in rules/README.md).
# Checked on the paragraphs under review; each hit is a WARN to read, not a ban.
WORDING = {
    "ai-words": ("Words that models overuse", (
        "delve", "underscore", "underscores", "intricate", "tapestry", "testament", "landscape",
        "pivotal", "showcase", "showcases", "foster", "fosters", "leverage", "leverages", "realm",
        "seamless", "seamlessly", "crucial", "enhance", "enhances", "garner", "meticulous",
        "meticulously", "vibrant", "valuable", "interplay", "bolster", "bolstered", "additionally")),
    "hype": ("Significance hype; state the concrete result", (
        "paves the way", "pave the way", "pivotal step", "crucial step", "revolutionize",
        "opens new avenues", "open new avenues", "sheds light on", "shed light on",
        "of paramount importance", "bridges the gap", "bridge the gap", "plays a key role",
        "plays a crucial role", "plays a vital role", "a new paradigm", "game-changing")),
    "intensifier": ("Empty intensifier or vague hedge; quantify or cut", (
        "extensive experiments", "comprehensive experiments", "a wide range of", "numerous",
        "very", "extremely", "highly", "quite", "really", "basically", "essentially", "actually",
        "somewhat", "relatively", "fairly", "to some extent", "a bit")),
    "boilerplate": ("Boilerplate emphasis or filler; let the sentence show it", (
        "it is worth noting", "it should be noted", "it should be emphasized", "it is important to note",
        "in order to", "the fact that", "to the best of our knowledge", "for the first time")),
    "copula": ("Use is, are or has", ("serves as", "stands as", "functions as", "acts as")),
    "nominalization": ("Put the action in the verb", (
        "perform an analysis", "performed an analysis", "conduct an evaluation", "conducted an evaluation",
        "make a comparison", "made a comparison", "provide a description", "give an explanation",
        "carry out an investigation", "achieve an improvement")),
    "overclaim": ("Verb stronger than the evidence; empirical results show or suggest", (
        "prove", "proves", "proven", "guarantee", "guarantees", "demonstrate", "demonstrates",
        "establish", "establishes", "confirm", "confirms", "significantly")),
}
FORMULAIC_OPENERS = ("In recent years", "Recently,", "With the rapid development", "With the development of",
                     "Despite recent advances", "Nowadays", "In this era")
CONNECTIVE_OPENERS = ("Moreover", "Furthermore", "Additionally", "In addition", "Also")
# "This shows" with no noun after the pronoun hides what "this" is.
BARE_DEMONSTRATIVE = r"^(?:This|These|That|Those)\s+(?:is|are|was|were|shows?|suggests?|indicates?|means|leads?|makes?|allows?|enables?|demonstrates?|implies)\b"
MAX_SENTENCE_WORDS = 30

MAX_BOLD_WORDS = 6
MAX_PARAGRAPH_HEADINGS = 4

WRITING_RULES = """\
1. Open each section or paragraph with the point it makes. Never open with
   "Table X reports" or "Figure X shows"; cite tables and figures in passing.
   A section may open with one sentence saying what it presents
   ("This section presents ..."); the author asked for this form.
2. Use plain words. A reader scanning once must understand the sentence.
3. Short sentences, one idea each. No comma chains.
4. An analysis states the phenomenon, then the reason, then the conclusion
   (what this shows is important or necessary). A phenomenon alone is not analysis.
5. Do not restate numbers that a table or figure already shows.
6. Every claim needs support from the paper's data, experiments or citations.
   Temporal coincidence is not causation.
7. Results sections discuss results. Method details belong in the method section.
8. A paragraph discusses the figure it cites. Do not mix in another figure's conclusion.
9. Keep the paper's existing terms. Do not invent new names for existing concepts.
10. Avoid absolute wording (always, never, obvious, eliminate).
11. Do not add filler such as "whether X translates into Y" when the answer is known.
12. Do not write "not X but Y" contrasts, parallel triplets, or explanations
    set off by colons, semicolons or dashes. State "A is B" directly.
13. Each section or subsection first says why (motivation), then how (what we did).
14. Introduction: background and significance, the problem and its difficulty,
    current work and its gap, our approach without technical detail, then the
    contributions (finding, method, results). Technical detail belongs in the method.
15. Related work: each subsection ends with the shortcoming of those methods and
    how our approach differs.
16. Experimental settings cover the data, metrics, implementation details and
    baselines that apply to this paper.
17. Every symbol is explained where it first appears; one symbol, one meaning.
18. Do not write SOTA or "state-of-the-art"; say the method improves results.
19. Do not cite a reference as the subject ("[3] shows"); name the method or authors.
20. Numbers for the same quantity agree everywhere in the paper.
21. Keep the subject close to its verb. Start a sentence with known context and end it
    with the new or important information (Gopen and Swan).
22. Put the action in the verb, not in a noun ("we analyze", not "we perform an analysis").
23. One term per concept. Do not cycle synonyms for the same thing (model, network,
    architecture); do not rename an existing concept.
24. The verb matches the evidence: results show or suggest; they do not prove or
    guarantee. Replace vague magnitudes with numbers or cut them.
25. Each sentence connects to the previous one (cause, contrast, consequence, refinement
    or example), and each paragraph supports the section's main claim.
26. No opening sentence that could start any paper ("Large language models have achieved
    remarkable success"). Start with the specific problem or result.
27. Contributions name specific results, not "we study X" or "extensive experiments".
28. A bold lead-in or list label states the point of its item. A generic category
    ("Performance", "Opponent state") carries no information.
29. A caption lets the reader understand the figure or table without the main text.
30. Hedge only where the evidence is uncertain; then use a calibrated verb (suggests,
    is consistent with). Do not stack qualifiers.
31. When the paragraph's section has a plan card, the paragraph serves that card's
    purpose and storyline. A paragraph that drifts to another point is a violation.
32. Nothing the evidence does not contain: no grouping, category, taxonomy, framing
    or qualifier that the figure, table or data does not show (four "kinds" of change
    the figure never marks; "manually inspected"), and nothing the plan card lists
    under "What it cannot claim".
33. A paragraph that discusses a figure, table or result moves in this order: why we
    look at this (the question), what was done (the setup), what we find, what it
    means, and what it cannot show. The figure or table is cited in passing inside
    that argument, never as the opening.
34. A method subsection opens with two or three sentences on the challenge it meets
    and why, citing one or two works, before the design.
35. The paper's own rules, given in the block "paper rules", hold like the rules above.
    Report a violation of one of them as rule 35 and quote the paper rule in the problem.
36. Say what the method does. A sentence that defines it by what it does not use or
    is not ("we do not use X", "not X but Y") is a violation.
"""


def rules_text(numbers) -> str:
    """Only the numbered writing rules given, in their original wording and order."""
    keep = set(numbers)
    items = re.split(r"(?m)^(?=\d+\. )", WRITING_RULES)
    return "".join(item for item in items if item[:item.find(".")].isdigit() and int(item[:item.find(".")]) in keep)
