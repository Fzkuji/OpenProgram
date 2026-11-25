"""A small interview project for the qualitative tests: four transcripts in four formats."""

import subprocess
import zipfile
from pathlib import Path

P1 = """Interviewer: How do you share your data?

P1: I always upload my data to a public repository after the paper is accepted.

Interviewer: Why only then?

P1: Honestly I worry that someone will scoop my results before I publish.

P1: Yeah.
"""
P2 = """# Field notes

I never share code because nobody in my lab has time to clean it up.

Our funder now requires a data management plan for every project.
"""
P3 = """WEBVTT

00:00:01.000 --> 00:00:04.000
<v Interviewer>Do you preregister?

00:00:05.000 --> 00:00:09.000
<v Ana>Preregistration felt like extra paperwork at first,

00:00:09.000 --> 00:00:12.000
<v Ana>but now I think it protects me from reviewers.
"""
DOCX_PARAS = ["Q: What would help you share more?",
              "A: Training in version control would help me share code more often."]
CLAIMS = """# Claims
Approved: yes

C1: Linguists share data late.
RQ: RQ1
RQ1: How do linguists decide whether to share their data and code?
"""


def make_docx(path: Path, paragraphs: list[str]) -> None:
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>')


def make_project(root: Path) -> Path:
    repo = root / "paper"
    data = repo / "data" / "interviews"
    data.mkdir(parents=True)
    (repo / "workflow").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (data / "p1.txt").write_text(P1)
    (data / "p2.md").write_text(P2)
    (data / "p3.vtt").write_text(P3)
    make_docx(data / "p4.docx", DOCX_PARAS)
    (data / "README.md").write_text("# About these interviews\n\nCC BY 4.0, collected in 2025.\n")
    (data / "audio.mp3").write_bytes(b"ID3")
    (repo / "workflow" / "claims.md").write_text(CLAIMS)
    return repo


IDS = ["p1.txt:3", "p1.txt:7", "p2.md:3", "p2.md:5", "p3.vtt:7", "p4.docx:2"]
