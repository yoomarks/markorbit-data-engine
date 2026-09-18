from types import SimpleNamespace

import pytest

from app.us_tsdr.cli import _plan


def test_legacy_autodiscovery_plan_fails_closed_without_explicit_opt_in() -> None:
    args = SimpleNamespace(
        allow_legacy_autodiscovery=False,
        capacity=10,
        backfill_bucket=1,
    )
    with pytest.raises(RuntimeError, match="explicit sparse acquisition intent"):
        _plan(args)
