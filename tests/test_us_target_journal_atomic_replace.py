from pathlib import Path

import pytest

import app.us.target_bulk_journal as bulk_journal
import app.us.target_canary_journal as canary_journal


@pytest.mark.parametrize("module", [canary_journal, bulk_journal])
def test_atomic_write_retries_transient_permission_error(tmp_path, monkeypatch, module):
    target = tmp_path / "journal.json"
    original_replace = Path.replace
    attempts = {"count": 0}

    def flaky_replace(self, destination):
        attempts["count"] += 1
        if attempts["count"] <= 2:
            raise PermissionError("transient Windows file lock")
        return original_replace(self, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    module._atomic_write(target, {"state": "SAFE"})

    assert target.is_file()
    assert attempts["count"] == 3
    assert not target.with_suffix(target.suffix + ".tmp").exists()


@pytest.mark.parametrize("module", [canary_journal, bulk_journal])
def test_atomic_write_fails_closed_after_bounded_permission_retries(tmp_path, monkeypatch, module):
    target = tmp_path / "journal.json"
    attempts = {"count": 0}

    def locked_replace(self, destination):
        attempts["count"] += 1
        raise PermissionError("persistent Windows file lock")

    monkeypatch.setattr(Path, "replace", locked_replace)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    with pytest.raises(PermissionError, match="persistent Windows file lock"):
        module._atomic_write(target, {"state": "SAFE"})

    assert attempts["count"] == 1 + len(module._ATOMIC_REPLACE_RETRY_DELAYS_SECONDS)
    assert not target.exists()
    assert target.with_suffix(target.suffix + ".tmp").is_file()
