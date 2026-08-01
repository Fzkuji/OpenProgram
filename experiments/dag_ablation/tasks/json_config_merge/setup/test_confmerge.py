import json
from confmerge import deep_merge, load_layers


def test_scalar_override():
    assert deep_merge({"a": 1, "b": 2}, {"b": 3}) == {"a": 1, "b": 3}


def test_recursive():
    base = {"x": {"y": 1, "z": 2}}
    assert deep_merge(base, {"x": {"z": 9, "w": 8}}) == {
        "x": {"y": 1, "z": 9, "w": 8}}


def test_list_replaced():
    assert deep_merge({"l": [1, 2, 3]}, {"l": [9]}) == {"l": [9]}


def test_none_deletes():
    assert deep_merge({"a": 1, "b": 2}, {"b": None}) == {"a": 1}
    assert deep_merge({"x": {"y": 1, "z": 2}}, {"x": {"y": None}}) == {
        "x": {"z": 2}}


def test_no_mutation():
    base = {"x": {"y": 1}}
    deep_merge(base, {"x": {"y": 2}})
    assert base == {"x": {"y": 1}}


def test_load_layers(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"k": 1, "d": {"p": 1}}))
    b.write_text(json.dumps({"d": {"q": 2}, "k": None}))
    assert load_layers(str(a), str(b)) == {"d": {"p": 1, "q": 2}}
