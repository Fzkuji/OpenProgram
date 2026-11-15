"""Thresholds and the short list of exempt pages, measured on the current site."""
from __future__ import annotations

# The only pages allowed their own script and styles: the interaction is the content.
PROTOTYPE_PAGES = frozenset({
    "reference/design/runtime/agent-configuration/agent-core-configuration-ui.html",
    "reference/design/runtime/dag/live-layout.html",
    "reference/design/ui/sidebar/sidebar-luma-prototype.html",
    "reference/design/ui/workspace/project-workspace-prototype.html",
})

# Produced from code at build time. Everything else under docs/ is HTML.
GENERATED_PREFIXES = ("reference/cli/", "reference/config-keys", "reference/provider-registry")

PARAGRAPH_WORDS = (80, 60)            # error above, warning above
PAGE_PROSE_WORDS = (2000, 1500)
PROSE_SHARE = (0.70, 0.60)
PROSE_SHARE_MIN_WORDS = 150
COVERED_SENTENCE = (0.8, 0.7)         # error at or above, warning at or above
SIMILAR_SENTENCE = 0.8                # Jaccard, within and across pages
SENTENCE_MIN_CONTENT_WORDS = 6
BULLET_SECTION_ITEMS = (8, 6)         # error at or above, warning at or above
UNIFORM_TAB = (0.25, 0.15)            # error above, warning above
UNIFORM_TAB_MIN_PAGES = 10
BASELINE_REASON_MIN = 20
TABLE_CELL_WORDS = (140, 80)          # error above, warning above; measured, see the design page
SMALL_TABLE_ROWS = 4                  # header included; a table this short can be restated by one sentence
FOLD_REPEATS_SUMMARY = 0.8            # similarity above which a link-only fold says its summary twice
CLAIM_EXEMPTIONS = (94, 11)           # data-claim="example" markers, pages with covers none

# Pairs whose code spans differ on purpose. Each needs its reason in LOOSENED_BECAUSE.
CODE_SPANS_DIFFER = frozenset({
    "reference/design/memory/speaker-identity.html",
    "reference/design/runtime/dag/external-program-run-node.html",
    "reference/design/runtime/workflows/report-suite.html",
    "reference/design/ui/chat/tool-group-summary.html",
})

# Each exemption or looser value against the fixed reference names its reason here,
# keyed by the page or the constant. See governance/ratchet.py.
LOOSENED_BECAUSE: dict[str, str] = {
    "reference/design/memory/speaker-identity.html": "The Chinese page shows the full-width colon as a character; English describes it.",
    "reference/design/runtime/dag/external-program-run-node.html": "Quotes the status label the UI shows, which each language renders in its own words.",
    "reference/design/runtime/workflows/report-suite.html": "The report format is Chinese output text; only the Chinese page quotes it literally.",
    "reference/design/ui/chat/tool-group-summary.html": "Documents how Chinese summaries are worded, with examples only that language has.",
}
