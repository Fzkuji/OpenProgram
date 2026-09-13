"""Test teardown releases the canonical SessionStore after package extraction."""
import pytest

from openprogram.store.session import session_store
from tests.component.self_update.control.test_controller_bundle import _release_native_workspace
from tests.conftest import _drop_tmp_rooted_session_store


@pytest.mark.parametrize("native", [False, True])
def test_test_cleanup_releases_canonical_session_store(tmp_path, monkeypatch, native):
    store = session_store.SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(session_store.shared, "_default_store", store)
    try:
        for _ in range(2):
            if native:
                _release_native_workspace(tmp_path)
            else:
                cleanup = _drop_tmp_rooted_session_store.__wrapped__()
                next(cleanup)
                with pytest.raises(StopIteration):
                    next(cleanup)
            assert session_store.shared._default_store is None
        # The native workspace closes resources before deleting its directory;
        # the global fixture retains its existing reference-reset semantics.
        assert store._index_background_enabled is (not native)
    finally:
        store.close()
