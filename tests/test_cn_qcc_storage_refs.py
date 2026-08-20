from pathlib import Path

import pytest

from app.cn_qcc.storage_refs import (
    EXPORT_PREFIX,
    RESULT_PREFIX,
    export_object_key,
    export_path,
    resolve_object_key,
    result_object_key,
    result_path,
)


def test_portable_object_keys_are_host_independent(tmp_path: Path) -> None:
    batch_key = "CN_QCC_20260821_001"
    assert export_object_key(batch_key) == f"{EXPORT_PREFIX}/{batch_key}.tasks.csv"
    assert result_object_key(batch_key) == f"{RESULT_PREFIX}/{batch_key}.result.csv"
    assert export_path(tmp_path / "out", batch_key) == (tmp_path / "out" / f"{batch_key}.tasks.csv").resolve()
    assert result_path(tmp_path / "in", batch_key) == (tmp_path / "in" / f"{batch_key}.result.csv").resolve()


def test_object_key_resolution_rejects_cross_namespace_and_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        resolve_object_key(tmp_path, "cn_qcc/incoming/x.csv", prefix=EXPORT_PREFIX)
    with pytest.raises(ValueError):
        resolve_object_key(tmp_path, "cn_qcc/outgoing/../secret.csv", prefix=EXPORT_PREFIX)
    with pytest.raises(ValueError):
        resolve_object_key(tmp_path, "cn_qcc/outgoing/nested/x.csv", prefix=EXPORT_PREFIX)
