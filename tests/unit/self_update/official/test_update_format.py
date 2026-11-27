"""The content-addressed update format: objects, packs, range merging, layer manifests and pointers."""
from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
import pytest

from openprogram.host._platform.runtime_layout import Layout
from openprogram.operations.self_update.official import channel as channels
from openprogram.operations.self_update.official import objects, publish, signing
from openprogram.operations.self_update.official.objects import Entry, Layer, Location, ObjectStore
from openprogram.operations.self_update.official.repository import ReleaseError

PEM = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
TRUSTED = [signing.public_key_hex(PEM)]
COMMIT = "ab" * 20


def _readers(*blobs: bytes) -> dict:
    return {objects.sha256_bytes(blob): (lambda blob=blob: blob) for blob in blobs}


def test_a_pack_holds_each_object_as_its_own_frame(tmp_path):
    blobs = [b"one\n", b"two\n" * 1000, b""]
    name, where = objects.write_pack(tmp_path, _readers(*blobs), level=3)
    assert objects.PACK.fullmatch(name)
    data = (tmp_path / name).read_bytes()
    assert name == f"pack-{objects.sha256_bytes(data)[:12]}.pack"
    for blob in blobs:
        digest = objects.sha256_bytes(blob)
        spot = where[digest]
        assert spot.pack == name
        assert objects.unpack_object(data[spot.offset:spot.offset + spot.length], digest) == blob
    assert objects.write_pack(tmp_path, {}) == (None, {})


def _written(tmp_path, layer, blobs, reuse=None):
    """Write ``layer`` as one pack; returns the pack's directory, name, root tree and its place."""
    root = {}

    def trailer(here, add):
        layer.objects = {**layer.objects, **here}
        root["digest"] = objects.write_trees(layer, add, reuse=reuse)[0]

    name, where = objects.write_pack(tmp_path, _readers(*blobs), level=3, trailer=trailer)
    return name, root["digest"], where[root["digest"]]


def _fetcher(directory, log=None):
    def fetch(wanted):
        if log is not None:
            log.append(set(wanted))
        found = {}
        for digest, where in wanted.items():
            data = (directory / where.pack).read_bytes()
            found[digest] = objects.unpack_object(data[where.offset:where.offset + where.length], digest)
        return found
    return fetch


def test_a_layer_is_a_tree_of_directory_objects_ending_its_pack(tmp_path):
    blob = b"content\n"
    digest = objects.sha256_bytes(blob)
    entries = [Entry("a/b/c.txt", 0o644, len(blob), digest), Entry("a/top.txt", 0o755, len(blob), digest),
               Entry("link", symlink="a/top.txt")]
    name, root, where = _written(tmp_path, Layer("code", {"commit": COMMIT}, entries), [blob])
    log = []
    layer = objects.read_layer("code", root, where, _fetcher(tmp_path, log))
    assert layer.entries == sorted(entries, key=lambda entry: entry.path)
    assert layer.meta == {"commit": COMMIT}
    assert layer.objects[digest].pack == name
    # One fetch per level: the root, then a/, then a/b/.
    assert [len(wanted) for wanted in log] == [1, 1, 1]
    assert len(layer.tree_digests()) == 3


def test_an_unchanged_directory_keeps_its_tree(tmp_path):
    one, two = b"one\n", b"two\n"
    entries = [Entry("deep/x/unchanged.txt", 0o644, 4, objects.sha256_bytes(one)),
               Entry("top/changed.txt", 0o644, 4, objects.sha256_bytes(one))]
    first_pack, first_root, first_where = _written(tmp_path, Layer("code", {}, list(entries)), [one])
    first = objects.read_layer("code", first_root, first_where, _fetcher(tmp_path))
    entries[1] = Entry("top/changed.txt", 0o644, 4, objects.sha256_bytes(two))
    second_layer = Layer("code", {}, entries, dict(first.objects))
    _pack, root, where = _written(tmp_path, second_layer, [two], reuse=first.trees)
    log = []
    second = objects.read_layer("code", root, where, _fetcher(tmp_path, log))
    deep = [digest for digest, place in first.trees.values() if place.pack == first_pack]
    assert set(deep) & second.tree_digests() and first_root not in second.tree_digests()
    shared = first.tree_digests() & second.tree_digests()
    assert len(shared) == 2  # deep/ and deep/x/; the root and top/ changed.


def test_a_layer_in_another_format_is_refused_by_name():
    data = json.dumps({"schema": objects.SCHEMA - 1, "layer": "code", "meta": {}, "entries": []}).encode()
    digest = objects.sha256_bytes(data)
    with pytest.raises(objects.OtherSchema, match=f"layer code is in layer format 2; this build reads format {objects.SCHEMA}"):
        objects.read_layer("code", digest, Location("pack-0123456789ab.pack", 0, 9), lambda wanted: {digest: data})


def test_uploaded_layer_records_of_every_shell_platform_are_recognised():
    for platform in publish.SHELL_PLATFORMS:
        assert publish.LAYER_RECORD.fullmatch(f"shell-{platform}-1.6.0.json"), platform
    assert publish.LAYER_RECORD.fullmatch("runtime-linux-arm64-py3.12-0123456789abcdef.json")
    assert not publish.LAYER_RECORD.fullmatch("shell-windows-arm64-1.6.0.json.bak")


def test_a_release_asks_shell_layers_only_for_the_apps_it_published():
    def manifest(*paths):
        return {"schema": 1, "files": [{"path": path} for path in paths]}

    every = manifest("desktop-thin-mac-arm64/OpenProgram-1.6.0-mac-arm64.zip",
                     "desktop-thin-mac-x86_64/OpenProgram-1.6.0-mac-x64.zip",
                     "desktop-thin-win-x86_64/OpenProgram-1.6.0-win-x64.exe",
                     "desktop-thin-linux-x86_64/OpenProgram-1.6.0-linux-x86_64.AppImage",
                     "desktop-thin-linux-arm64/OpenProgram-1.6.0-linux-arm64.AppImage")
    assert publish.released_shell_platforms(every) == set(publish.SHELL_PLATFORMS)
    partial = manifest("desktop-thin-mac-arm64/OpenProgram-1.6.0-mac-arm64-unsigned.zip",
                       "desktop-full-mac-x86_64/OpenProgram-1.6.0-mac-x64.zip",
                       "desktop-thin-win-x86_64/OpenProgram-1.6.0-win-x64.exe",
                       "desktop-thin-linux-arm64/OpenProgram-1.6.0-linux-arm64.deb",
                       "release-manifest.json")
    assert publish.released_shell_platforms(partial) == {"windows-x86_64"}, \
        "an unsigned or full macOS App, or a Linux leg with only its deb, keeps the installer"


def test_a_tree_refuses_unsafe_names_and_links(tmp_path):
    digest = objects.sha256_bytes(b"x")
    where = Location("pack-0123456789ab.pack", 0, 9)

    def root(entries):
        data = json.dumps({"schema": objects.SCHEMA, "layer": "runtime-macos-arm64", "meta": {},
                           "entries": entries}).encode()
        return objects.sha256_bytes(data), data

    def read(entries, name="runtime-macos-arm64"):
        digest_, data = root(entries)
        return objects.read_layer(name, digest_, where, lambda wanted: {digest_: data})

    good = {"name": "x", "mode": 0o755, "size": 1, "sha256": digest, "at": ["", 0, 9]}
    assert read([good]).objects[digest] == where
    for bad in ("..", ".", "a/b", "", "a\\b"):
        with pytest.raises(ReleaseError):
            read([{**good, "name": bad}])
    with pytest.raises(ReleaseError, match="twice"):
        read([good, good])
    with pytest.raises(ReleaseError, match="outside"):
        read([{"name": "python", "symlink": "../etc/passwd"}])
    with pytest.raises(ReleaseError, match="location"):
        read([{**good, "at": ["elsewhere", 0, 9]}])
    with pytest.raises(ReleaseError, match="not 'code'"):
        read([good], name="code")


def test_a_tampered_object_is_refused(tmp_path):
    name, where = objects.write_pack(tmp_path, _readers(b"real"), level=3)
    other = objects.write_pack(tmp_path / "other", _readers(b"fake"), level=3)[1]
    frame = (tmp_path / "other" / other[objects.sha256_bytes(b"fake")].pack).read_bytes()
    with pytest.raises(ReleaseError, match="does not match its name"):
        objects.unpack_object(frame, objects.sha256_bytes(b"real"))
    with pytest.raises(ReleaseError, match="could not be decompressed"):
        objects.unpack_object(b"garbage", objects.sha256_bytes(b"real"))


def test_close_ranges_merge_and_far_ones_do_not():
    wanted = [("a", 0, 10), ("b", 10, 5), ("c", 100, 5), ("d", 10_000_000, 7)]
    spans = objects.merge_ranges(wanted, gap=1000)
    assert [(span.start, span.end) for span in spans] == [(0, 105), (10_000_000, 10_000_007)]
    assert [member[0] for member in spans[0].members] == ["a", "b", "c"]


def test_too_many_ranges_join_across_the_smallest_gaps():
    wanted = [(str(index), index * 1000 + (500 if index == 3 else 0), 10) for index in range(6)]
    spans = objects.merge_ranges(wanted, gap=0, limit=3)
    assert len(spans) == 3
    assert sum(len(span.members) for span in spans) == 6
    assert all(span.start <= offset and offset + length <= span.end
               for span in spans for _name, offset, length in span.members)


def test_the_store_verifies_adopts_places_and_prunes(tmp_path):
    store = ObjectStore(tmp_path / "objects")
    blob = b"#!/bin/sh\necho hi\n"
    digest = objects.sha256_bytes(blob)
    with pytest.raises(ReleaseError):
        store.put(digest, b"other")
    local = tmp_path / "local"
    local.write_bytes(blob)
    assert store.adopt(digest, local) == store.path(digest)
    assert store.adopt(objects.sha256_bytes(b"nope"), local) is None
    target = tmp_path / "out" / "run.sh"
    objects.place(store.path(digest), target, 0o755)
    assert target.read_bytes() == blob and os.stat(target).st_mode & 0o777 == 0o755
    target.write_bytes(b"changed")  # A placed file is its own, never the store's.
    assert store.read(digest) == blob
    other = store.put(objects.sha256_bytes(b"other"), b"other")
    assert store.prune({digest}) == 1 and not other.exists() and store.has(digest)


def test_a_large_file_is_published_and_joined_as_pieces(tmp_path):
    size = objects.CHUNKED_FROM + 1000
    data = os.urandom(size)
    entry, readers = objects.bytes_entry("bin/big", 0o755, data)
    assert [length for _digest, length in entry.chunks] == [objects.CHUNK_SIZE] * 4 + [1000]
    assert set(readers) == {digest for digest, _size in entry.chunks}, "the pieces are the objects"
    small, small_readers = objects.bytes_entry("bin/small", 0o644, b"x" * 10)
    assert small.chunks == () and set(small_readers) == {small.sha256}
    source = tmp_path / "big"
    source.write_bytes(data)
    assert objects.file_entry(source, "bin/big", 0o755)[0] == entry

    blobs = [reader() for reader in {**readers, **small_readers}.values()]
    _name, root, where = _written(tmp_path, Layer("runtime-macos-arm64", {}, [entry, small]), blobs)
    read = objects.read_layer("runtime-macos-arm64", root, where, _fetcher(tmp_path))
    assert read.entries == [entry, small] and set(read.objects) == set(readers) | {small.sha256}
    assert read.sizes()[entry.chunks[-1][0]] == 1000

    # A new version that changed its first and last bytes: the pieces between come from the old one.
    store = ObjectStore(tmp_path / "objects")
    changed = b"NEW!" + data[4:-10] + b"0123456789"
    new, new_readers = objects.bytes_entry("bin/big", 0o755, changed)
    store.take_pieces(new, source)
    assert [store.has(digest) for digest, _size in new.chunks] == [False, True, True, True, False]
    assert not store.join(new), "not while a piece is missing"
    for digest, _size in new.chunks:
        if not store.has(digest):
            store.put(digest, new_readers[digest]())
    assert store.join(new) and store.read(new.sha256) == changed

    def tree(chunks):
        data = json.dumps({"schema": objects.SCHEMA, "layer": "runtime-macos-arm64", "meta": {}, "entries": [
            {"name": "big", "mode": 0o755, "size": size, "sha256": entry.sha256, "chunks": chunks}]}).encode()
        digest = objects.sha256_bytes(data)
        return objects.read_layer("runtime-macos-arm64", digest, Location("pack-0123456789ab.pack", 0, 9),
                                  lambda wanted: {digest: data})

    pieces = [[digest, length, ["", 0, 9]] for digest, length in entry.chunks]
    assert tree(pieces).entries[0].chunks == entry.chunks
    for bad in (pieces[:-1], [*pieces[:-1], [pieces[-1][0], 999, ["", 0, 9]]], None):
        with pytest.raises(ReleaseError, match="piece"):
            tree(bad)


def test_scanning_a_tree_keeps_links_and_modes(tmp_path):
    root = tmp_path / "tree"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "tool").write_text("t")
    (root / "bin" / "tool").chmod(0o755)
    (root / "bin" / "alias").symlink_to("tool")
    entries, sources = objects.scan_tree(root, "frontend/", git_ids=True)
    by_path = {entry.path: entry for entry in entries}
    assert by_path["frontend/bin/tool"].mode == 0o755 and by_path["frontend/bin/alias"].symlink == "tool"
    assert by_path["frontend/bin/tool"].git == "32f64f4d836716819dc5fa9a1e09a29b428881df"  # git hash-object of "t"
    assert set(sources) == {by_path["frontend/bin/tool"].sha256}
    (root / "bin" / "out").symlink_to("/etc/passwd")
    with pytest.raises(ReleaseError, match="outside"):
        objects.scan_tree(root)


# --- Pointers -----------------------------------------------------------------------------


def _pointer(sequence=5, name=channels.ANNOUNCED, **changes) -> tuple[bytes, bytes]:
    fields = {
        "sequence": sequence, "published_at": "2026-11-26T08:00:00Z", "version": "1.5.1.2", "announce": True,
        "build": COMMIT[:12], "notes": "notes", "commit": COMMIT, "shell_compat": 1,
        "packs": {"pack-0123456789ab.pack": {"bytes": 100, "sha256": "0" * 64}},
        "layers": {"code": {"manifest": "1" * 64, "pack": "pack-0123456789ab.pack", "offset": 0, "length": 10}},
        **changes,
    }
    data = channels.encode(fields, name=name)
    return data, signing.sign(data, PEM).encode("ascii")


def _parse(data, signature, name=channels.ANNOUNCED, **options):
    return channels.parse(data, signature, name=name, **options)


def test_a_pointer_is_read_only_with_a_trusted_signature():
    data, signature = _pointer()
    found = _parse(data, signature, trusted_keys=TRUSTED)
    assert found.sequence == 5 and found.name == "1.5.1.2" and found.layers["code"].manifest == "1" * 64
    with pytest.raises(ReleaseError, match="signature"):
        _parse(data.replace(b"1.5.1.2", b"1.5.1.3"), signature, trusted_keys=TRUSTED)
    other = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    with pytest.raises(ReleaseError, match="trusted key"):
        _parse(data, signature, trusted_keys=[signing.public_key_hex(other)])
    with pytest.raises(ReleaseError, match="no release signing key"):
        _parse(data, signature, trusted_keys=[])


def test_a_pointer_file_carries_its_signature_and_its_own_name():
    data = channels.encode(json.loads(_pointer()[0]), name=channels.LATEST)
    blob = channels.seal(data, PEM)
    assert channels.open_pointer(blob, name=channels.LATEST, trusted_keys=TRUSTED).pointer == channels.LATEST
    with pytest.raises(ReleaseError, match="not channel.json"):
        channels.open_pointer(blob, name=channels.ANNOUNCED, trusted_keys=TRUSTED)
    quiet = channels.seal(channels.encode({**json.loads(data), "announce": False}, name=channels.ANNOUNCED), PEM)
    with pytest.raises(ReleaseError, match="not announced"):
        channels.open_pointer(quiet, name=channels.ANNOUNCED, trusted_keys=TRUSTED)
    envelope = json.loads(blob)
    envelope["signed"] = envelope["signed"].replace("1.5.1.2", "1.5.1.3")
    with pytest.raises(ReleaseError, match="signature"):
        channels.open_pointer(json.dumps(envelope).encode(), name=channels.LATEST, trusted_keys=TRUSTED)


def test_a_missing_pointer_reads_as_nothing_published(tmp_path):
    assert channels.read(channels.DirectoryTransport(tmp_path), trusted_keys=TRUSTED) is None


def test_this_build_trusts_the_release_key_and_not_the_test_key():
    assert signing.RELEASE_PUBLIC_KEYS and not set(TRUSTED) & set(signing.RELEASE_PUBLIC_KEYS)
    data, signature = _pointer()
    with pytest.raises(ReleaseError, match="signature"):
        _parse(data, signature)


@pytest.mark.parametrize("changes, expected", [
    ({"version": "1.5"}, "invalid version"),
    ({"build": "0" * 12}, "invalid build"),
    ({"sequence": 0}, "no sequence"),
    ({"layers": {}}, "no code layer"),
    ({"layers": {"code": {"manifest": "1" * 64, "pack": "pack-0123456789ab.pack", "offset": 95, "length": 10}}},
     "malformed location"),
    ({"layers": {"code": {"manifest": "1" * 64, "pack": "pack-ffffffffffff.pack", "offset": 0, "length": 10}}},
     "malformed location"),
])
def test_a_malformed_pointer_is_refused(changes, expected):
    data, signature = _pointer(**changes)
    with pytest.raises(ReleaseError, match=expected):
        _parse(data, signature, trusted_keys=TRUSTED)


def test_an_older_sequence_than_one_seen_is_refused(tmp_path):
    layout = Layout(tmp_path / "runtime")
    layout.root.mkdir()
    newer = _parse(*_pointer(7), trusted_keys=TRUSTED)
    older = _parse(*_pointer(6), trusted_keys=TRUSTED)
    channels.note_seen(layout, newer)
    channels.note_seen(layout, newer)  # The same pointer read again is fine.
    with pytest.raises(ReleaseError, match="older than 7"):
        channels.note_seen(layout, older)
    assert channels.seen_sequence(layout) == 7


def test_the_pointer_name_follows_the_environment(monkeypatch):
    monkeypatch.delenv(channels.POINTER_VARIABLE, raising=False)
    assert channels.pointer_name() == "channel.json"
    monkeypatch.setenv(channels.POINTER_VARIABLE, "latest")
    assert channels.pointer_name() == "latest.json"


def test_plain_http_is_refused_except_on_this_machine():
    with pytest.raises(ReleaseError, match="HTTPS"):
        channels.HttpTransport("http://example.com/updates")
    assert channels.HttpTransport("http://127.0.0.1:9/updates").local
    assert channels.UPDATES_URL == "https://github.com/Fzkuji/OpenProgram/releases/download/updates"


@pytest.mark.parametrize("tag, feature", [("v1.2.0", True), ("v2.0.0", True), ("v1.2.1", False), ("v1.2.1.1", False),
                                          ("v1.2.0.1", False)])
def test_a_release_tag_says_whether_it_builds_installers(tag, feature):
    assert publish.feature_release(tag) is feature


def test_unreferenced_packs_are_those_nothing_may_still_read():
    def pointer(sequence, packs):
        data, signature = _pointer(sequence, packs={name: {"bytes": 100, "sha256": "0" * 64} for name in packs},
                                   layers={"code": {"manifest": "1" * 64, "pack": packs[0], "offset": 0, "length": 1}})
        return _parse(data, signature, trusted_keys=TRUSTED)

    names = [f"pack-{index:012x}.pack" for index in range(8)]
    history = [pointer(index + 1, [names[index]]) for index in range(5)]
    announced = pointer(2, [names[1]])
    latest = pointer(9, [names[6]])
    dropped = publish.unreferenced([*names, "channel.json"], history, [latest, announced], pending=[names[7]], keep=3)
    assert dropped == [names[0], names[5]]


def test_an_object_larger_than_its_size_is_not_unpacked(tmp_path):
    import zstandard

    blob = b"x" * 1000
    frame = zstandard.ZstdCompressor().compress(blob)
    digest = objects.sha256_bytes(blob)
    assert objects.unpack_object(frame, digest, 1000) == blob
    with pytest.raises(ReleaseError, match="larger than"):
        objects.unpack_object(frame, digest, 10)
    # A frame that lies about its size is cut off all the same.
    huge = zstandard.ZstdCompressor(write_content_size=False).compress(b"\0" * (objects.MAX_TREE_BYTES + 10))
    with pytest.raises(ReleaseError, match="larger than"):
        objects.unpack_object(huge, "0" * 64)


def test_a_link_chain_may_not_climb_out_of_its_layer():
    links = {"a/b": "..", "x": "a/b/..", "ok": "a/b/c", "loop": "loop2", "loop2": "loop"}
    assert objects.resolve_link("ok", links) == "c"
    with pytest.raises(ReleaseError, match="outside"):
        objects.resolve_link("x", links)
    with pytest.raises(ReleaseError, match="loop"):
        objects.resolve_link("loop", links)
    layer = Layer("code", {}, [Entry("frontend/up", symlink="../source/x"), Entry("source/x", 0o644, 1, "0" * 64)])
    objects.check_links(layer)
    with pytest.raises(ReleaseError, match="outside frontend"):
        objects.materialize(layer, ObjectStore(Path("/nonexistent")), Path("/nonexistent"), prefix="frontend/")


def test_an_unverifiable_update_source_is_ignored(monkeypatch):
    from openprogram.operations.self_update.github_release import github

    monkeypatch.setattr(github, "_curl_release_bytes", lambda url: b"not signed" if url.endswith(".sig") else b"{}")
    assert github.update_source() is None
