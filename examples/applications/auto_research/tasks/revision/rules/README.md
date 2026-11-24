# rules

What a revision must satisfy. Change rules here; the checks read them.

| File | Source | Contents |
| --- | --- | --- |
| `venue.py` | IAB camera-ready email, paper repository | Main file, style lines, authors, page limit, required section, page geometry. |
| `writing.py` | AML guide, author's review, public writing guides | Rejected terms, discouraged words, comma limit, absolute words, expression word lists (`WORDING`), sentence length, and the numbered `WRITING_RULES` the reviewer reads. |
| `latex.py` | AML guide sections 4 and 6 to 9, author's layout rules | Float placement, reference words, label prefixes, placeholders, figure font size, last-line limits. |
| `checklist.py` | All three | One row per requirement and how it is checked: `auto` (named checks), `review` (numbered writing rules), `manual`, `built-in`, `n/a`. |

A new requirement needs a row in `checklist.py`. An `auto` row names checks that exist in `steps/checks/`; a `review` row names rules in `WRITING_RULES`. `tests/test_rules.py` enforces both.

## Public writing guides

The expression rules (`WORDING`, openers, sentence length, `WRITING_RULES` 21 to 30) come from these sources, read on 2026-10-30. Rules that conflict with the author's requirements were left out; for example, the guides allow colons and occasional triples, which the author rejected.

| Source | Stars | Used for |
| --- | --- | --- |
| [Orchestra-Research/AI-Research-SKILLs](https://github.com/Orchestra-Research/AI-Research-SKILLs), `ml-paper-writing/references/writing-guide.md` | 14.1k | Gopen and Swan reader expectations, Perez micro rules (pronouns, filler words), Lipton (generic openings, hedging), Steinhardt (one term per concept), contribution bullets, self-contained captions. |
| [Master-cai/Research-Paper-Writing-Skills](https://github.com/Master-cai/Research-Paper-Writing-Skills) | 7.9k | One message per paragraph, sentence-to-sentence relations, reverse outlining, table rules. |
| [blader/humanizer](https://github.com/blader/humanizer) | 62.3k | Words models overuse, inflated significance, copula avoidance, bold labels that carry no information, forced triads, dashes. |
| [AIScientists-Dev/academic-humanizer](https://github.com/AIScientists-Dev/academic-humanizer) | 3.0k | Over-claiming verbs, significance hype, empty intensifiers, novelty padding, formulaic openers, connective chains, clause-stacked sentences over 30 words, evidence-tied hedging. |
| [MLNLP-World/Paper-Writing-Tips](https://github.com/MLNLP-World/Paper-Writing-Tips) | 4.7k | Already covered by the AML guide, which cites it. |
