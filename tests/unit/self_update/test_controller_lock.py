"""Controller locking works with native file APIs on each host."""
from openprogram.self_update.supervisor import _controller_lock


def test_controller_lock_excludes_a_second_owner_and_releases(tmp_path):
    with _controller_lock(tmp_path) as acquired:
        assert acquired
        with _controller_lock(tmp_path) as duplicate:
            assert not duplicate
        # An unsuccessful contender must not unlock the active owner.
        with _controller_lock(tmp_path) as another:
            assert not another
    with _controller_lock(tmp_path) as acquired_again:
        assert acquired_again
