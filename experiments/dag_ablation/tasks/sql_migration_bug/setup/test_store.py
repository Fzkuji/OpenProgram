from store import Store


def test_put_get():
    s = Store()
    s.put("a", "1")
    assert s.get("a") == "1"
    assert s.get("missing") is None
    assert s.get("missing", "d") == "d"


def test_overwrite():
    s = Store()
    s.put("a", "1")
    s.put("a", "2")
    assert s.get("a") == "2"


def test_persists_across_connections(tmp_path):
    p = str(tmp_path / "db.sqlite")
    s = Store(p)
    s.put("k", "v")
    assert Store(p).get("k") == "v"


def test_recent():
    s = Store()
    for v in "12345":
        s.put("a", v)
    assert s.recent("a") == ["5", "4", "3"]
    assert s.recent("a", 2) == ["5", "4"]
    assert s.recent("nope") == []
