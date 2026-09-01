from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
import sys

import pytest

from scripts.check_feature_matrix import MatrixError, check_matrix


ROOT = Path(__file__).resolve().parents[3]
MATRIX = ROOT / "docs/reference/design/feature-matrix.html"
FRAMEWORK_COMPARISON = ROOT / "docs/reference/design/framework-comparison.html"


def _demote_json_schema_row(text: str) -> str:
    start = text.index('<tr><td class="fname">按 JSON schema 约束输出')
    old = '<td class="g1 us">●</td>'
    cell = text.index(old, start)
    return text[:cell] + '<td class="g2 us">◐</td>' + text[cell + len(old) :]


def _rename_section_item(text: str, section: str, old: str) -> str:
    start = text.index(f'<h2 id="{section}"')
    end = text.index("<h2", start + 4)
    section_text = text[start:end]
    changed = section_text.replace(old, "伪造的明细项", 1)
    assert changed != section_text
    return text[:start] + changed + text[end:]


def test_feature_matrix_published_values_match_canonical_table() -> None:
    result = check_matrix(MATRIX)

    assert result.feature_count == 160
    assert result.openprogram_score == 94.5
    assert result.openprogram_gaps == 56
    assert result.openprogram_only == 6
    assert result.json_schema_status == "●"
    assert result.snapshot == "2fb471b3"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda text: text.replace("2fb471b3", "deadbeef"), "snapshot"),
        (
            lambda text: re.sub(
                r"(?:main|integrated candidate)@[0-9a-f]{8}",
                "integrated candidate@cafebabe",
                text,
            ),
            "integration snapshot",
        ),
        (
            lambda text: text.replace("OpenProgram为94.5分", "OpenProgram为999分", 1),
            "score",
        ),
        (
            lambda text: text.replace(
                "参考列已确认的 56 项", "参考列已确认的 999 项", 1
            ),
            "gaps",
        ),
        (
            lambda text: text.replace(
                "仅 OpenProgram 确认的 6 项", "仅 OpenProgram 确认的 999 项", 1
            ),
            "OpenProgram-only",
        ),
        (
            lambda text: text.replace(
                '<circle cx="500" cy="8" r="5" fill="#4f8ef7"',
                '<circle cx="999" cy="8" r="5" fill="#4f8ef7"',
                1,
            ),
            "category point",
        ),
        (
            lambda text: _rename_section_item(text, "gaps", "终端快捷键自动配置"),
            "gap detail",
        ),
        (
            lambda text: text.replace(
                '进入后续评估</b> <span style="color:#6b6a63">29 项',
                '进入后续评估</b> <span style="color:#6b6a63">999 项',
                1,
            ),
            "gap detail group count",
        ),
        (
            lambda text: _rename_section_item(
                text, "ours", "函数调用树写进<br>同一张会话图"
            ),
            "OpenProgram-only detail",
        ),
        (_demote_json_schema_row, "JSON Schema"),
    ],
)
def test_feature_matrix_checker_rejects_published_drift(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    original = MATRIX.read_text(encoding="utf-8")
    changed = mutation(original)
    assert changed != original
    candidate = tmp_path / "feature-matrix.html"
    candidate.write_text(changed, encoding="utf-8")

    with pytest.raises(MatrixError, match=message):
        check_matrix(candidate)

    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_feature_matrix.py"), str(candidate)],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    assert completed.returncode == 1
    assert message in completed.stderr


def test_framework_documents_pin_the_same_current_release_versions() -> None:
    expected = {
        "anomalyco/opencode": "v1.18.25",
        "anthropics/claude-code": "v2.1.252",
        "openai/codex": "rust-v0.152.0",
        "openclaw/openclaw": "v2026.8.1",
    }

    def versions(path: Path) -> dict[str, str]:
        found: dict[str, set[str]] = {}
        for repo, tag in re.findall(
            r"https://github\.com/([^/]+/[^/]+)/releases/tag/([^\"<]+)",
            path.read_text(encoding="utf-8"),
        ):
            found.setdefault(repo, set()).add(tag)
        assert all(len(tags) == 1 for tags in found.values())
        return {repo: next(iter(tags)) for repo, tags in found.items()}

    assert versions(MATRIX) == expected
    assert versions(FRAMEWORK_COMPARISON) == expected


def test_framework_documents_publish_the_current_event_count() -> None:
    module = ast.parse(
        (ROOT / "openprogram/events/registry.py").read_text(encoding="utf-8")
    )
    event_dict = next(
        node.value
        for node in module.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "EVENTS"
    )
    assert isinstance(event_dict, ast.Dict)
    count = len(event_dict.keys)

    matrix = MATRIX.read_text(encoding="utf-8")
    comparison = FRAMEWORK_COMPARISON.read_text(encoding="utf-8")
    assert f"我们 {count} 个" in matrix
    assert comparison.count(f"{count}事件") == 2


def test_runtime_docs_publish_structured_return_and_error_contracts() -> None:
    english = (ROOT / "docs/reference/api/runtime.md").read_text(encoding="utf-8")
    chinese = (ROOT / "docs/reference/api/runtime.zh.md").read_text(encoding="utf-8")

    for text in (english, chinese):
        assert "StructuredOutputSchemaError" in text
        assert "StructuredOutputValidationError" in text
        assert "StructuredOutputGenerationError" in text
        assert "StructuredOutputUnsupportedError" in text
        assert "invalid_schema" in text
        assert "invalid_json" in text
        assert "validation_failed" in text
        assert "missing_submission" in text
        assert "mixed_submission" in text
        assert "incomplete" in text
        assert "refusal" in text
        assert "unsupported" in text
        assert "response_format=None" in text
        assert "Python JSON" in text

    for text in (english, chinese):
        assert "stream_fn=None) -> Any" in text
        assert "timeout_s=None, on_retry=None) -> Any" in text
        assert "stream_fn=None) -> str" not in text
        assert "timeout_s=None, on_retry=None) -> str" not in text
        assert (
            'Runtime._call(content, model="default", response_format=None) -> Any'
            in text
        )
        assert (
            'Runtime._async_call(content, model="default", response_format=None) -> Any'
            in text
        )
        assert (
            'Runtime._call(content, model="default", response_format=None) -> str'
            not in text
        )
        assert (
            'Runtime._async_call(content, model="default", response_format=None) -> str'
            not in text
        )
    assert "With `response_format=None`, returns" in english
    assert "`response_format=None` 时返回" in chinese
