"""The commercial plagiarism database check of text_overlap, with HTTP replaced by a fake
post that answers in the documented shapes of Winston AI and Originality.ai."""

import email.message
import io
import json
import urllib.error

import pytest

from auto_research.tasks.overlap import providers

from .test_workflow import ARX, make, no_service_keys, offline, run  # noqa: F401  fixtures

UNCITED = "https://example.com/blog/memory"


def winston_answer(text, score=8.0, sources=None):
    n = len(text.split())
    return {"status": 200, "scanInformation": {"service": "plagiarism", "scanTime": "", "inputType": "text",
                                               "language": "en"},
            "result": {"score": score, "sourceCounts": len(sources or []), "textWordCounts": n,
                       "totalPlagiarismWords": sum(s["plagiarismWords"] for s in sources or []),
                       "identicalWordCounts": 0, "similarWordCounts": 0},
            "sources": sources or [], "attackDetected": {"zero_width_space": False, "homoglyph_attack": False},
            "text": text, "similarWords": [], "citations": [], "indexes": [], "credits_used": 2 * n,
            "credits_remaining": 1000}


def winston_source(url, title, plagiarism_words, sequence, citation=False, excluded=False):
    return {"score": 0, "canAccess": True, "url": url, "title": title, "plagiarismWords": plagiarism_words,
            "identicalWordCounts": plagiarism_words, "similarWordCounts": 0, "author": None, "description": None,
            "publishedDate": None, "source": None, "citation": citation, "is_excluded": excluded,
            "plagiarismFound": [{"startIndex": 0, "endIndex": len(sequence), "sequence": sequence}]}


def originality_answer(score, phrases):
    return {"results": {"properties": {"privateID": 1, "id": "x", "title": "t", "excludedUrls": [],
                                       "publicLink": "", "content": "", "formattedContent": ""},
                        "credits": {"used": 1},
                        "plagiarism": {"score": score, "results": [
                            {"phrase": phrase, "results": [
                                {"link": link, "title": title, "scores": [{"score": 0.9, "sentence": phrase}],
                                 "cite_info": {"authors": [], "publisher": None,
                                               "identifiers": {"doi": None, "isbn": None}},
                                 "timestamps": {"date_modified": None, "date_published": None},
                                 "source_category": "web", "submission_meta": None}]}
                            for phrase, link, title in phrases]}}}


def http_error(code, body):
    headers = email.message.Message()
    return urllib.error.HTTPError("https://x", code, "err", headers, io.BytesIO(json.dumps(body).encode()))


@pytest.fixture
def service(monkeypatch):
    """Record every request; the answer comes from service.answer(url, body, headers)."""
    class Service:
        calls = []
        answer = None
    monkeypatch.setattr(providers, "sleep", lambda s: None)

    def post(url, body, headers, timeout=providers.TIMEOUT):
        Service.calls.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        result = Service.answer(url, body, headers)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(providers, "post", post)
    return Service


def opt_in(repo, value="yes"):
    (repo / "workflow" / "text_overlap.md").write_text(f"Send to plagiarism service: {value}\n")


def test_winston_request_and_uncited_source_fail(tmp_path, offline, service, monkeypatch):
    monkeypatch.setenv("WINSTONAI_API_KEY", "w-key")
    repo = make(tmp_path)
    opt_in(repo)
    service.answer = lambda url, body, headers: winston_answer(body["text"], 9.5, [
        winston_source(UNCITED, "A memory blog", 12, "agents that run for days"),
        winston_source("https://arxiv.org/abs/2401.11111", "Hierarchical Episodic Memory for Agents", 40, ARX),
        winston_source("https://excluded.org", "Excluded", 90, "x", excluded=True)])
    out, data = run(repo)
    call = service.calls[0]
    assert call["url"] == "https://api.gowinston.ai/v2/plagiarism"
    assert call["headers"] == {"Authorization": "Bearer w-key"}
    assert set(call["body"]) == {"text", "language", "country"} and len(call["body"]["text"]) >= 100
    # only the prose: no bibliography, citation keys, comments or math
    sent = call["body"]["text"]
    assert "Not On Arxiv" not in sent and "smith" not in sent and "comment" not in sent and "frac" not in sent
    assert "Hierarchical summarization compresses" in sent
    c = data["commercial"]
    assert c["status"] == "done" and c["provider"] == "winston" and c["score"] == 9.5 and c["chunks"] == 1
    by_url = {m["url"]: m for m in c["matches"]}
    assert "https://excluded.org" not in by_url
    blog, arxiv = by_url[UNCITED], by_url["https://arxiv.org/abs/2401.11111"]
    assert blog["percent"] == round(100 * 12 / c["words"], 1) >= 5 and not blog["cited"]
    assert arxiv["cited"] and arxiv["excerpt"].startswith("Hierarchical")      # cited by the bibliography
    assert out["status"] == "FAIL" and any("does not cite" in p and UNCITED in p for p in data["problems"])
    assert len(c["problems"]) == 1 and out["commercial"]["status"] == "done"
    assert "## 商业查重库" in open(out["report"]).read()


def test_originality_request_shape_and_cited_source_passes(tmp_path, offline, service, monkeypatch):
    monkeypatch.setenv("ORIGINALITY_API_KEY", "o-key")
    repo = make(tmp_path)
    opt_in(repo)
    service.answer = lambda url, body, headers: originality_answer(12, [
        (ARX[:120], "https://arxiv.org/abs/2401.11111v2", "Hierarchical Episodic Memory for Agents")])
    out, data = run(repo)
    call = service.calls[0]
    assert call["url"] == "https://api.originality.ai/api/v3/scan" and call["headers"] == {"X-OAI-API-KEY": "o-key"}
    body = call["body"]
    assert body["check_plagiarism"] is True and body["storeScan"] is False
    assert not any(body[k] for k in ("check_ai", "check_facts", "check_readability", "check_grammar"))
    assert body["aiModelVersion"] == "model-5.0" and body["title"].startswith("Adaptive Memory")
    c = data["commercial"]
    assert c["status"] == "done" and c["provider"] == "originality" and c["score"] == 12.0
    assert c["matches"][0]["percent"] >= 5 and c["matches"][0]["cited"]
    assert out["status"] == "PASS_WITH_NOTES" and not c["problems"]
    assert c["not_configured"] == ["Winston AI (WINSTONAI_API_KEY / WINSTON_API_KEY)",
                                 "Turnitin / iThenticate (TCA) (TCA_BASE_URL + TCA_API_KEY)"]


def test_overall_similarity_at_limit_fails(tmp_path, offline, service, monkeypatch):
    monkeypatch.setenv("WINSTONAI_API_KEY", "w-key")
    repo = make(tmp_path)
    opt_in(repo)
    service.answer = lambda url, body, headers: winston_answer(body["text"], 25.0)
    out, data = run(repo)
    assert out["status"] == "FAIL" and "overall similarity 25.0%" in data["commercial"]["problems"][0]


def test_without_opt_in_nothing_is_sent(tmp_path, offline, service, monkeypatch):
    monkeypatch.setenv("WINSTONAI_API_KEY", "w-key")
    repo = make(tmp_path, note="- earlier.txt: extended version.")
    out, data = run(repo)
    assert service.calls == [] and data["commercial"]["status"] == "not_opted_in"
    notes = (repo / "workflow" / "text_overlap.md").read_text()
    assert notes == "- earlier.txt: extended version.\nSend to plagiarism service: no\n"
    assert out["status"] == "PASS_WITH_NOTES"
    assert "Send to plagiarism service: yes" in open(out["report"]).read()
    # still no: the line is kept, nothing sent
    run(repo)
    assert service.calls == [] and (repo / "workflow" / "text_overlap.md").read_text() == notes


def test_rules_choose_the_provider_or_turn_it_off(tmp_path, offline, service, monkeypatch):
    monkeypatch.setenv("WINSTONAI_API_KEY", "w-key")
    monkeypatch.setenv("ORIGINALITY_API_KEY", "o-key")
    repo = make(tmp_path)
    opt_in(repo)
    service.answer = lambda url, body, headers: originality_answer(0, [])
    (repo / "workflow" / "rules.json").write_text(json.dumps({"notes": {"plagiarism_provider": "originality"}}))
    _, data = run(repo)
    assert data["commercial"]["provider"] == "originality" and len(service.calls) == 1
    (repo / "workflow" / "rules.json").write_text(json.dumps({"notes": {"plagiarism_provider": "none"}}))
    _, data = run(repo)
    assert data["commercial"]["status"] == "off" and len(service.calls) == 1
    (repo / "workflow" / "rules.json").write_text(json.dumps({"notes": {"plagiarism_provider": "copyleaks"}}))
    with pytest.raises(ValueError, match="plagiarism_provider"):
        run(repo)


def test_named_provider_without_key_is_not_configured(tmp_path, offline, service, monkeypatch):
    monkeypatch.setenv("WINSTONAI_API_KEY", "w-key")
    repo = make(tmp_path)
    opt_in(repo)
    (repo / "workflow" / "rules.json").write_text(json.dumps({"notes": {"plagiarism_provider": "originality"}}))
    out, data = run(repo)
    assert service.calls == [] and data["commercial"]["status"] == "not_configured"
    assert out["status"] == "PASS_WITH_NOTES"
    assert "Originality.ai (ORIGINALITY_API_KEY)" in open(out["report"]).read()


def test_refused_request_is_a_note_not_a_failure(tmp_path, offline, service, monkeypatch):
    monkeypatch.setenv("WINSTONAI_API_KEY", "w-key")
    repo = make(tmp_path)
    opt_in(repo)
    service.answer = lambda url, body, headers: http_error(
        402, {"error": "PAYMENT_REQUIRED", "description": "Insufficient credits"})
    out, data = run(repo)
    assert len(service.calls) == 1          # 4xx other than 429 is not retried
    c = data["commercial"]
    assert c["status"] == "error" and "HTTP 402" in c["note"] and "Insufficient credits" in c["note"]
    assert out["status"] == "PASS_WITH_NOTES"
    assert "没有给出结果" in open(out["report"]).read()


def test_rate_limit_and_timeouts_are_retried(service, monkeypatch):
    monkeypatch.setenv("WINSTONAI_API_KEY", "w-key")
    text = "Agents remember what they need. " * 10
    answers = iter([http_error(429, {"error": "TOO_MANY_REQUESTS"}), TimeoutError(),
                    winston_answer(text.strip(), 3.0)])
    service.answer = lambda url, body, headers: next(answers)
    assert providers.PROVIDERS["winston"].scan(text, "t")["score"] == 3.0
    assert len(service.calls) == 3 and service.calls[0]["timeout"] == providers.TIMEOUT

    service.calls.clear()
    service.answer = lambda url, body, headers: http_error(503, {"error": "SERVICE_UNAVAILABLE"})
    with pytest.raises(providers.ProviderError, match="HTTP 503"):
        providers.PROVIDERS["winston"].scan(text, "t")
    assert len(service.calls) == providers.TRIES

    service.answer = lambda url, body, headers: {"unexpected": True}
    with pytest.raises(providers.ProviderError, match="no result"):
        providers.PROVIDERS["winston"].scan(text, "t")


def test_long_text_goes_in_chunks(service, monkeypatch):
    monkeypatch.setenv("ORIGINALITY_API_KEY", "o-key")
    sentence = "Retrieval stays fast as the memory of the agent grows over many episodes."
    text = " ".join([sentence] * 400)                       # about 30 000 characters
    scores = iter([30.0, 0.0, 0.0, 0.0])
    service.answer = lambda url, body, headers: originality_answer(next(scores), [
        (sentence, UNCITED, "Blog")] if len(service.calls) == 1 else [])
    found = providers.PROVIDERS["originality"].scan(text, "Paper")
    sizes = [len(c["body"]["content"]) for c in service.calls]
    assert len(sizes) == found["chunks"] >= 3 and max(sizes) <= providers.Originality.chunk_chars
    assert " ".join(c["body"]["content"] for c in service.calls) == text
    assert service.calls[0]["body"]["title"] == f"Paper (1/{len(sizes)})"
    first = len(service.calls[0]["body"]["content"].split())
    assert found["score"] == round(30.0 * first / found["words"], 1) and found["words"] == len(text.split())
    assert found["matches"][0]["matched_words"] == len(sentence.split())


def test_chunks_cut_between_sentences_and_inside_long_ones():
    assert providers.chunks("One two. Three four. Five six.", 20) == ["One two. Three four.", "Five six."]
    long = "word " * 30
    parts = providers.chunks("Short. " + long, 40)
    assert all(len(p) <= 40 for p in parts) and " ".join(parts).split() == ("Short. " + long).split()


def test_missing_key_is_named_and_raises_inside_scan(monkeypatch):
    provider, missing = providers.select("")
    assert provider is None and len(missing) == 3
    with pytest.raises(providers.ProviderError, match="no key"):
        providers.PROVIDERS["originality"].scan("text " * 50, "t")
    monkeypatch.setenv("WINSTON_API_KEY", "w")
    provider, missing = providers.select("")
    assert provider.name == "winston" and missing == ["Originality.ai (ORIGINALITY_API_KEY)", "Turnitin / iThenticate (TCA) (TCA_BASE_URL + TCA_API_KEY)"]
