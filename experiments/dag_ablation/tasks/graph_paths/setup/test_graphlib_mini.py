import pytest
from graphlib_mini import topo_sort, shortest_path, components

G = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": [], "e": []}


def test_topo():
    assert topo_sort(G) == ["a", "b", "c", "d", "e"]


def test_topo_cycle():
    with pytest.raises(ValueError):
        topo_sort({"a": ["b"], "b": ["a"]})


def test_path():
    assert shortest_path(G, "a", "d") == ["a", "b", "d"]
    assert shortest_path(G, "a", "a") == ["a"]
    assert shortest_path(G, "d", "a") is None
    assert shortest_path(G, "a", "e") is None


def test_components():
    assert components(G) == [["a", "b", "c", "d"], ["e"]]
