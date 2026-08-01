from wrapper import wrap, indent


def test_empty():
    assert wrap("", 10) == []


def test_single_word():
    assert wrap("hello", 10) == ["hello"]


def test_exact_fit():
    assert wrap("ab cd", 5) == ["ab cd"]


def test_basic():
    assert wrap("the quick brown fox jumps", 10) == [
        "the quick", "brown fox", "jumps"]


def test_long_word_gets_own_line():
    assert wrap("a supercalifragilistic b", 5) == [
        "a", "supercalifragilistic", "b"]


def test_indent_skips_nothing():
    assert indent("a\nb", "> ") == "> a\n> b"
