from bus import EventBus


def test_pub_sub_order():
    b, seen = EventBus(), []
    b.subscribe("t", lambda p: seen.append(("a", p)))
    b.subscribe("t", lambda p: seen.append(("b", p)))
    assert b.publish("t", 1) == 2
    assert seen == [("a", 1), ("b", 1)]


def test_unknown_topic():
    assert EventBus().publish("nope", 1) == 0


def test_unsubscribe_idempotent():
    b, seen = EventBus(), []
    off = b.subscribe("t", lambda p: seen.append(p))
    off()
    off()
    assert b.publish("t", 1) == 0
    assert seen == []


def test_errors_isolated():
    b, seen = EventBus(), []

    def boom(p):
        raise RuntimeError("x")

    b.subscribe("t", boom)
    b.subscribe("t", lambda p: seen.append(p))
    assert b.publish("t", 1) == 2
    assert seen == [1]
    assert len(b.errors) == 1
    assert isinstance(b.errors[0], RuntimeError)
    b.publish("t", 2)
    assert len(b.errors) == 1


def test_wildcard():
    b, seen = EventBus(), []
    b.subscribe("*", lambda t, p: seen.append((t, p)))
    b.subscribe("x", lambda p: seen.append(("direct", p)))
    assert b.publish("x", 7) == 2
    assert ("x", 7) in seen and ("direct", 7) in seen
    b.publish("y", 8)
    assert ("y", 8) in seen
