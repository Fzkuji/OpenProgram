from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_feature_matrix import MatrixError, check_matrix


ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/reference/design/feature-matrix.html"


def test_feature_matrix_mechanics_match_displayed_summary() -> None:
    result = check_matrix(MATRIX)

    assert result.feature_count == 160
    assert all(len(row.cells) == 13 for row in result.rows)
    assert all(cell in {"●", "·"} for row in result.rows for cell in row.cells)
    assert result.displayed_metrics == result.recomputed_metrics


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda text: text.replace("●</td>", "◐</td>", 1), "invalid symbol"),
        (
            lambda text: text.replace('<td class="g1">●</td></tr>', "</tr>", 1),
            "13 framework cells",
        ),
        (
            lambda text: text.replace(
                'data-matrix-metric="openprogram-gaps" data-value="',
                'data-matrix-metric="openprogram-gaps" data-value="999',
                1,
            ),
            "openprogram-gaps",
        ),
        (
            lambda text: text.replace(
                'data-matrix-metric="openprogram-score" data-value="',
                'data-matrix-metric="openprogram-score" data-value="999',
                1,
            ),
            "openprogram-score",
        ),
    ],
)
def test_feature_matrix_checker_rejects_mechanical_drift(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    changed = mutation(MATRIX.read_text(encoding="utf-8"))
    candidate = tmp_path / "feature-matrix.html"
    candidate.write_text(changed, encoding="utf-8")

    with pytest.raises(MatrixError, match=message):
        check_matrix(candidate)
