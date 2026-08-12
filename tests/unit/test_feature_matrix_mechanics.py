from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from scripts.check_feature_matrix import MatrixError, check_matrix


ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/reference/design/feature-matrix.html"


def _promote_json_schema_row(text: str) -> str:
    start = text.index('<tr><td class="fname">按 JSON schema 约束输出')
    old = '<td class="g2 us">◐</td>'
    cell = text.index(old, start)
    return text[:cell] + '<td class="g1 us">●</td>' + text[cell + len(old) :]


def test_feature_matrix_published_values_match_canonical_table() -> None:
    result = check_matrix(MATRIX)

    assert result.feature_count == 160
    assert result.openprogram_score == 78.0
    assert result.openprogram_gaps == 73
    assert result.openprogram_only == 6
    assert result.json_schema_status == "◐"
    assert result.snapshot == "2fb471b3"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda text: text.replace("2fb471b3", "deadbeef"), "snapshot"),
        (
            lambda text: text.replace("OpenProgram为78分", "OpenProgram为999分", 1),
            "score",
        ),
        (
            lambda text: text.replace(
                "参考列已确认的 73 项", "参考列已确认的 999 项", 1
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
                '>任务与规划 11</text><line x1="500"',
                '>任务与规划 11</text><line x1="999"',
                1,
            ),
            "category point",
        ),
        (_promote_json_schema_row, "JSON Schema"),
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
