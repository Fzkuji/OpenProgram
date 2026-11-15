from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_feature_matrix import MatrixError, check_matrix


ROOT = Path(__file__).resolve().parents[3]
MATRIX = ROOT / "docs/reference/design/feature-matrix.html"


@pytest.fixture(params=["", ".zh"])
def matrix_path(request) -> Path:
    return MATRIX.with_name(f"feature-matrix{request.param}.html")


def test_ssrf_matrix_evidence_matches_integrated_snapshot(matrix_path: Path) -> None:
    result = check_matrix(matrix_path)

    assert result.feature_count == 160
    assert result.openprogram_score == 92.0
    assert result.openprogram_gaps == 59
    assert result.openprogram_only == 6


def _replace_in_ssrf_row(text: str, old: str, new: str) -> str:
    start = text.index("Private-network access and SSRF protection" if "<title>Feature matrix</title>" in text else "私网访问与 SSRF 防护")
    end = text.index("</tr>", start)
    return text[:start] + text[start:end].replace(old, new, 1) + text[end:]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("be5eaa3c", "cafebabe", "SSRF snapshot"),
        ("<td>◐</td>", "<td>●</td>", "SSRF status"),
    ],
)
def test_ssrf_matrix_checker_rejects_stale_evidence(
    tmp_path: Path,
    matrix_path: Path,
    old: str,
    new: str,
    message: str,
) -> None:
    original = matrix_path.read_text(encoding="utf-8")
    changed = _replace_in_ssrf_row(original, old, new)
    assert changed != original
    candidate = tmp_path / "feature-matrix.html"
    candidate.write_text(changed, encoding="utf-8")

    with pytest.raises(MatrixError, match=message):
        check_matrix(candidate)
