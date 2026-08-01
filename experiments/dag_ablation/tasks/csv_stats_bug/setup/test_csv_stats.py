import math
import pytest
from csv_stats import load_column, mean, median, summary

CSV = "n,value\na,1.5\nb,2.5\nc,\nd,4\n"


@pytest.fixture
def data(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text(CSV)
    return str(p)


def test_load_column_floats(data):
    assert load_column(data, "value") == [1.5, 2.5, 4.0]


def test_mean_empty():
    assert mean([]) == 0.0


def test_median_even():
    assert median([1, 2, 3, 4]) == 2.5
    assert median([3, 1, 2]) == 2


def test_summary(data):
    s = summary(data, "value")
    assert s["n"] == 3
    assert math.isclose(s["mean"], 8.0 / 3)
    assert s["median"] == 2.5
