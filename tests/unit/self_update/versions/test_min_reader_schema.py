"""How old a reader a SQLite store still accepts."""
import sqlite3

from openprogram.state.store import reader_floor


def _store(floor=None):
    connection = sqlite3.connect(":memory:")
    if floor is not None:
        reader_floor.record_floor(connection, floor)
    return connection


def test_a_store_with_no_floor_has_none():
    assert reader_floor.stored_floor(_store()) is None


def test_a_recorded_floor_is_read_back():
    assert reader_floor.stored_floor(_store(7)) == 7


def test_a_newer_store_opens_when_the_floor_allows_this_reader():
    assert reader_floor.readable_when_newer(_store(5), 5)


def test_a_raised_floor_refuses_older_code():
    assert not reader_floor.readable_when_newer(_store(6), 5)


def test_a_newer_store_with_no_floor_is_refused():
    assert not reader_floor.readable_when_newer(_store(), 5)


def test_the_floor_never_goes_down():
    connection = _store(40)
    reader_floor.record_floor(connection, 3)
    assert reader_floor.stored_floor(connection) == 40
