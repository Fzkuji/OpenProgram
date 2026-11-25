"""Full text of non-arXiv references: the author's PDFs first, then OpenAlex's open-access copy."""

from auto_research.tasks.citations import sources
from auto_research.tasks.deepread import source as deepread_source
from auto_research.tasks.overlap import gather, library

BIB = """@article{smith2020journal,
  title={Memory Consolidation in Long Running Agents},
  author={Smith, Ann}, journal={JMLR}, year={2020}, doi={10.5555/jmlr.2020.1}
}
@article{lee2019,
  title={Selective Forgetting for Sequential Decision Makers},
  author={Lee, Bo}, journal={TPAMI}, year={2019}
}
@article{kim2018,
  title={A Work Nobody Has},
  author={Kim, C}, journal={X}, year={2018}
}
"""
BODY = "agents keep a buffer of experience and decide what to forget by usage " * 8


def project(tmp_path, monkeypatch):
    repo = tmp_path / "paper"
    (repo / "workflow" / "papers").mkdir(parents=True)
    (repo / "main.tex").write_text("\\documentclass{article}\\begin{document}Hi\\end{document}")
    (repo / "refs.bib").write_text(BIB)
    (repo / "workflow" / "papers" / "lee-selective.pdf").write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setenv("ZOTERO_STORAGE", str(tmp_path / "no-zotero"))
    pages = {"lee-selective.pdf": "Selective Forgetting for\nSequential Decision Makers\nBo Lee\n" + BODY}
    monkeypatch.setattr(library, "pdf_pages", lambda path, first_only=False: pages.get(path.name, BODY))
    return repo


def test_local_pdf_by_title_then_open_access_by_doi(tmp_path, monkeypatch):
    repo = project(tmp_path, monkeypatch)
    asked = []

    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        asked.append(url)
        if url == "https://api.openalex.org/works/doi:10.5555/jmlr.2020.1":
            return {"id": "W1", "best_oa_location": {"pdf_url": "https://jmlr.org/papers/smith20.pdf"}}
        if url == "https://api.openalex.org/works" and "Memory" in params["search"]:
            return {"results": []}
        if url == "https://api.openalex.org/works":
            return {"results": [{"title": "Something else entirely", "best_oa_location": {"pdf_url": "https://x/y.pdf"}}]}
        return None

    monkeypatch.setattr(sources, "fetch", fetch)
    monkeypatch.setattr(deepread_source, "fetch_bytes", lambda url, label="", tries=3: b"%PDF-1.4 oa")
    found = gather.library_sources(repo, set(), tmp_path / "cache", [], 10)
    by_key = {s["file"]: s for s in found}
    assert set(by_key) == {"smith2020journal", "lee2019"}           # kim2018: neither has it
    assert by_key["lee2019"]["level"] == "local" and by_key["lee2019"]["url"] == ""
    assert by_key["smith2020journal"]["level"] == "open_access"
    assert by_key["smith2020journal"]["url"] == "https://jmlr.org/papers/smith20.pdf"
    assert by_key["smith2020journal"]["words"]
    # a title search hit that does not match the title is not used
    assert "https://api.openalex.org/works" in asked


def test_file_name_with_the_citation_key_or_doi_matches(tmp_path):
    files = [{"path": tmp_path / "smith2020journal.pdf", "name": "smith2020journal.pdf", "first": ""},
             {"path": tmp_path / "10.5555_jmlr.2021.9.pdf", "name": "10.5555_jmlr.2021.9.pdf", "first": ""}]
    assert library.local_pdf({"key": "smith2020journal", "title": "t"}, files) == files[0]["path"]
    assert library.local_pdf({"key": "zz", "doi": "10.5555/jmlr.2021.9", "title": "t"}, files) == files[1]["path"]
    assert library.local_pdf({"key": "zz", "title": "Unrelated Title Here"}, files) is None


def test_a_landing_page_declares_its_pdf(monkeypatch):
    def fetch(url, params=None, as_json=True, tries=3, label="", headers=None):
        if url == "https://api.openalex.org/works":
            return {"results": [
                {"title": "Dropout: a simple way to prevent neural networks from overfitting", "best_oa_location": None,
                 "locations": [{"landing_page_url": "https://jmlr.org/papers/v15/srivastava14a.html", "pdf_url": None},
                               {"landing_page_url": "https://mirror.org/srivastava14a.pdf", "pdf_url": None}]},
                {"title": "Another paper", "locations": [{"pdf_url": "https://wrong.org/a.pdf"}]}]}
        if url == "https://jmlr.org/papers/v15/srivastava14a.html":
            return '<html><meta name="citation_pdf_url" content="http://jmlr.org/papers/volume15/srivastava14a/srivastava14a.pdf"></html>'
        return None

    monkeypatch.setattr(sources, "fetch", fetch)
    urls = library.open_access_urls({"key": "d", "title": "Dropout: A Simple Way to Prevent Neural Networks from Overfitting"})
    assert urls == ["https://mirror.org/srivastava14a.pdf",
                    "http://jmlr.org/papers/volume15/srivastava14a/srivastava14a.pdf"]
