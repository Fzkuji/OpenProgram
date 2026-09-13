import logging

from openprogram.providers import metadata
from openprogram.providers.sources import models_dev


def test_malformed_provider_json_warns_without_content(tmp_path, monkeypatch, caplog):
    provider_dir = tmp_path / "broken"
    provider_dir.mkdir()
    secret = "secret-provider-json-value"
    (provider_dir / "provider.json").write_text("{" + secret, encoding="utf-8")
    monkeypatch.setattr(metadata, "_ROOT", tmp_path)

    with caplog.at_level(logging.WARNING):
        assert metadata._endpoints("broken") == {}

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.source == "provider_json"
    assert record.error_type == "JSONDecodeError"
    assert record.path == str(provider_dir / "provider.json")
    assert secret not in caplog.text


def test_wrong_shaped_provider_json_warns_once_when_enumerated(
    tmp_path, monkeypatch, caplog
):
    provider_dir = tmp_path / "broken"
    provider_dir.mkdir()
    (provider_dir / "provider.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(metadata, "_ROOT", tmp_path)

    with caplog.at_level(logging.WARNING):
        assert metadata.shipped_provider_ids() == []

    assert [(record.source, record.error_type) for record in caplog.records] == [
        ("provider_json", "TypeError")
    ]


def test_missing_provider_json_remains_silent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(metadata, "_ROOT", tmp_path)

    with caplog.at_level(logging.WARNING):
        assert metadata._endpoints("missing") == {}

    assert caplog.records == []


def test_invalid_utf8_provider_json_warns_and_falls_back(tmp_path, monkeypatch, caplog):
    provider_dir = tmp_path / "broken"
    provider_dir.mkdir()
    (provider_dir / "provider.json").write_bytes(b"\xffSECRET")
    monkeypatch.setattr(metadata, "_ROOT", tmp_path)

    with caplog.at_level(logging.WARNING):
        assert metadata._endpoints("broken") == {}

    assert [(record.source, record.error_type) for record in caplog.records] == [
        ("provider_json", "UnicodeDecodeError")
    ]
    assert "SECRET" not in caplog.text


def test_provider_json_disappearing_during_read_remains_silent(tmp_path, monkeypatch, caplog):
    provider_dir = tmp_path / "broken"
    provider_dir.mkdir()
    path = provider_dir / "provider.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(metadata, "_ROOT", tmp_path)
    monkeypatch.setattr(
        metadata.Path,
        "read_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError(path)),
    )

    with caplog.at_level(logging.WARNING):
        assert metadata._endpoints("broken") == {}

    assert caplog.records == []


def test_provider_json_warning_omits_exception_message(tmp_path, monkeypatch, caplog):
    provider_dir = tmp_path / "broken"
    provider_dir.mkdir()
    (provider_dir / "provider.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(metadata, "_ROOT", tmp_path)
    monkeypatch.setattr(
        metadata.Path,
        "read_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("SECRET_EXCEPTION_MESSAGE")),
    )

    with caplog.at_level(logging.WARNING):
        assert metadata._endpoints("broken") == {}

    assert caplog.records[0].error_type == "OSError"
    assert "SECRET_EXCEPTION_MESSAGE" not in caplog.text


def test_malformed_models_dev_cache_warns_without_content(
    tmp_path, monkeypatch, caplog
):
    path = tmp_path / "models_dev.json"
    secret = "secret-model-cache-value"
    path.write_text("{" + secret, encoding="utf-8")
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: path)

    with caplog.at_level(logging.WARNING):
        assert models_dev._read_disk_cache() == {}

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.source == "models_dev_cache"
    assert record.error_type == "JSONDecodeError"
    assert record.path == str(path)
    assert secret not in caplog.text


def test_wrong_shaped_models_dev_cache_warns(tmp_path, monkeypatch, caplog):
    path = tmp_path / "models_dev.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: path)

    with caplog.at_level(logging.WARNING):
        assert models_dev._read_disk_cache() == {}

    assert [(record.source, record.error_type) for record in caplog.records] == [
        ("models_dev_cache", "TypeError")
    ]


def test_missing_models_dev_cache_remains_silent(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: tmp_path / "missing.json")

    with caplog.at_level(logging.WARNING):
        assert models_dev._read_disk_cache() == {}

    assert caplog.records == []


def test_invalid_utf8_models_dev_cache_warns_and_falls_back(tmp_path, monkeypatch, caplog):
    path = tmp_path / "models_dev.json"
    path.write_bytes(b"\xffSECRET")
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: path)

    with caplog.at_level(logging.WARNING):
        assert models_dev._read_disk_cache() == {}

    assert [(record.source, record.error_type) for record in caplog.records] == [
        ("models_dev_cache", "UnicodeDecodeError")
    ]
    assert "SECRET" not in caplog.text


def test_models_dev_warning_omits_exception_message(tmp_path, monkeypatch, caplog):
    path = tmp_path / "models_dev.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(models_dev, "_disk_cache_path", lambda: path)
    monkeypatch.setattr(
        type(path),
        "read_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("SECRET_EXCEPTION_MESSAGE")),
    )

    with caplog.at_level(logging.WARNING):
        assert models_dev._read_disk_cache() == {}

    assert caplog.records[0].error_type == "OSError"
    assert "SECRET_EXCEPTION_MESSAGE" not in caplog.text


def test_models_dev_path_failure_uses_stable_unresolved_value(monkeypatch, caplog):
    monkeypatch.setattr(
        models_dev,
        "_disk_cache_path",
        lambda: (_ for _ in ()).throw(PermissionError("SECRET_PATH_FAILURE")),
    )

    with caplog.at_level(logging.WARNING):
        assert models_dev._read_disk_cache() == {}

    assert caplog.records[0].path == "<unresolved>"
    assert caplog.records[0].error_type == "PermissionError"
    assert "SECRET_PATH_FAILURE" not in caplog.text
