"""LaTeX rules: the AML guide sections 4, 6, 7, 8 and 9, and the author's layout rules."""

# Figures and tables in the main body go at the top of the page.
TOP_PLACEMENTS = ("t", "!t")

REF_WORDS = ("Section", "Figure", "Table", "Equation", "Algorithm", "Appendix")
LABEL_PREFIXES = ("sec:", "fig:", "tab:", "equ:", "eq:", "alg:", "app:")
SMALL_WORDS = {"a", "an", "the", "of", "and", "or", "for", "in", "on", "at", "to", "by",
               "via", "with", "as", "vs", "from", "into", "but", "nor"}

# Review notes and placeholders that must not remain in the paper.
PLACEHOLDERS = (r"\bTBD\b", r"\bTODO\b", r"\\todo\b", r"\\tbd\b", r"\?\?", r"\bXXX\b",
                r"\[cite\]", r"\\zc\b", r"\\zxy\{", r"\\textcolor\{red\}", r"\\color\{red\}")
COMMENT_MACROS = (r"\zxy", r"\zc")

# Text inside figures, at printed size, is not smaller than this.
MIN_FIGURE_FONT_PT = 7.0
# The last line of a paragraph or caption has at least this many words and this share of a line.
MIN_LAST_LINE_WORDS = 4
MIN_LAST_LINE_SHARE = 0.5
