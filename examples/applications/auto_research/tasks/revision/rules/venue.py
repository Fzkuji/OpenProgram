"""This paper and its venue: the IAB camera-ready email and the paper repository."""

MAIN_TEX = "0main.tex"
# Extra entry files that must also compile (the version with the appendix).
EXTRA_TEX = ("0main_appendix.tex",)
BIB_FILES = ("references.bib",)
BODY_PAGE_LIMIT = 5

REQUIRED_SOURCE = (
    r"\usepackage[sglblindworkshop, final]{neurips_2026}",
    r"\workshoptitle{Interpreting Agent Behavior (IAB) -- GLEE Competition Track}",
)
FORBIDDEN_SOURCE = ("Anonymous Author", r"\linenumbers")
REQUIRED_SECTIONS = ("Agent Behavior Analysis",)

# Camera-ready author block: every author, the two emails, the corresponding author.
REQUIRED_AUTHORS = ("Zichuan Fu", "Junjia Qi", "Xian Wu", "Wenlin Zhang", "Yimin Deng",
                    "Kaifeng Guo", "Xiangyu Zhao", "City University of Hong Kong",
                    "Tencent Jarvis Lab", "zc.fu@my.cityu.edu.hk", "xianzhao@cityu.edu.hk",
                    r"\thanks{Corresponding author.}")

# NeurIPS page geometry and font sizes, used by the PDF checks.
TEXT_WIDTH_PT = 397.48
BODY_FONT_PT = 10.0
