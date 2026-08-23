from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

import pytest

from scripts.check_feature_matrix import MatrixError, check_matrix


ROOT = Path(__file__).resolve().parents[3]
MATRIX = ROOT / "docs/reference/design/feature-matrix.html"


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
    assert result.openprogram_score == 83.5
    assert result.openprogram_gaps == 68
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
            lambda text: text.replace("OpenProgram为83.5分", "OpenProgram为999分", 1),
            "score",
        ),
        (
            lambda text: text.replace(
                "参考列已确认的 68 项", "参考列已确认的 999 项", 1
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
                '<circle cx="433" cy="8" r="5" fill="#4f8ef7"',
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
