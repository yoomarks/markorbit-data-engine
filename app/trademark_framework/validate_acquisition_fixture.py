from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.trademark_framework.acquisition import (
    ACQUISITION_FRAMEWORK_VERSION,
    AcquisitionPage,
    AcquisitionPageRequest,
    AcquisitionStatus,
    materialize_acquisition,
)


@dataclass
class FakeCursorApi:
    adapter_id: str = "FAKE_CURSOR_API_V1"
    calls: list[AcquisitionPageRequest] = field(default_factory=list)

    def initial_cursor(self) -> str | None:
        return "cursor-1"

    def fetch_page(self, request: AcquisitionPageRequest) -> AcquisitionPage:
        self.calls.append(request)
        pages = {
            "cursor-1": AcquisitionPage(
                page_key="release:page:1",
                payload=b'{"records":[{"id":"A"}]}',
                next_cursor="cursor-2",
                media_type="application/json",
            ),
            "cursor-2": AcquisitionPage(
                page_key="release:page:2",
                payload=b'{"records":[{"id":"B"}]}',
                next_cursor="cursor-3",
                media_type="application/json",
            ),
            "cursor-3": AcquisitionPage(
                page_key="release:page:3",
                payload=b'{"records":[{"id":"C"}]}',
                next_cursor=None,
                media_type="application/json",
            ),
        }
        return pages[request.cursor]


@dataclass
class LoopingApi:
    adapter_id: str = "FAKE_LOOPING_API_V1"

    def initial_cursor(self) -> str | None:
        return "loop"

    def fetch_page(self, request: AcquisitionPageRequest) -> AcquisitionPage:
        return AcquisitionPage(
            page_key="loop:page:1",
            payload=b"{}",
            next_cursor="loop",
            media_type="application/json",
        )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="trademark-acquisition-fixture-") as temporary:
        root = Path(temporary)
        adapter = FakeCursorApi()

        first = materialize_acquisition(
            adapter=adapter,
            jurisdiction="JP",
            source_id="JPO_FAKE_API",
            session_key="2026-08-22",
            output_root=root,
            max_pages=2,
        )
        assert first.status == AcquisitionStatus.PARTIAL
        assert first.invocation_pages == 2
        assert first.cumulative_pages == 2
        assert len(adapter.calls) == 2
        assert [call.cursor for call in adapter.calls] == ["cursor-1", "cursor-2"]
        assert [page.sequence for page in first.pages] == [1, 2]
        assert all(len(page.sha256) == 64 for page in first.pages)
        assert all((first.ledger_path.parent / page.object_key).is_file() for page in first.pages)

        ledger_after_first = json.loads(first.ledger_path.read_text(encoding="utf-8"))
        assert ledger_after_first["framework_version"] == ACQUISITION_FRAMEWORK_VERSION
        assert ledger_after_first["status"] == "PARTIAL"
        assert ledger_after_first["next_cursor"] == "cursor-3"
        assert "authorization" not in json.dumps(ledger_after_first).lower()

        resumed = materialize_acquisition(
            adapter=adapter,
            jurisdiction="JP",
            source_id="JPO_FAKE_API",
            session_key="2026-08-22",
            output_root=root,
        )
        assert resumed.status == AcquisitionStatus.COMPLETE
        assert resumed.invocation_pages == 1
        assert resumed.cumulative_pages == 3
        assert len(adapter.calls) == 3
        assert adapter.calls[-1].cursor == "cursor-3"
        assert [page.page_key for page in resumed.pages] == [
            "release:page:1",
            "release:page:2",
            "release:page:3",
        ]

        calls_before_replay = len(adapter.calls)
        replay = materialize_acquisition(
            adapter=adapter,
            jurisdiction="JP",
            source_id="JPO_FAKE_API",
            session_key="2026-08-22",
            output_root=root,
        )
        assert replay.status == AcquisitionStatus.COMPLETE
        assert replay.invocation_pages == 0
        assert replay.cumulative_pages == 3
        assert len(adapter.calls) == calls_before_replay

        first_object = replay.ledger_path.parent / replay.pages[0].object_key
        original = first_object.read_bytes()
        first_object.write_bytes(b"tampered")
        tamper_blocked = False
        try:
            materialize_acquisition(
                adapter=adapter,
                jurisdiction="JP",
                source_id="JPO_FAKE_API",
                session_key="2026-08-22",
                output_root=root,
            )
        except RuntimeError as exc:
            tamper_blocked = "changed" in str(exc)
        assert tamper_blocked is True
        first_object.write_bytes(original)

        loop_blocked = False
        loop_root = root / "loop-case"
        try:
            materialize_acquisition(
                adapter=LoopingApi(),
                jurisdiction="CH",
                source_id="IPI_FAKE_API",
                session_key="loop-fixture",
                output_root=loop_root,
            )
        except RuntimeError as exc:
            loop_blocked = "did not advance" in str(exc)
        assert loop_blocked is True
        assert not list(loop_root.rglob("*.raw"))

        orphan_root = root / "orphan-case"
        orphan_object = (
            orphan_root
            / "JP"
            / "JPO_FAKE_API"
            / "orphan-fixture"
            / "objects"
            / "00000001-deadbeefdeadbeef.raw"
        )
        orphan_object.parent.mkdir(parents=True, exist_ok=True)
        orphan_object.write_bytes(b"old-unledgered-response")
        orphan_adapter = FakeCursorApi()
        orphan_blocked = False
        try:
            materialize_acquisition(
                adapter=orphan_adapter,
                jurisdiction="JP",
                source_id="JPO_FAKE_API",
                session_key="orphan-fixture",
                output_root=orphan_root,
                max_pages=1,
            )
        except RuntimeError as exc:
            orphan_blocked = "unledgered" in str(exc)
        assert orphan_blocked is True
        assert len(orphan_adapter.calls) == 1
        assert not (
            orphan_object.parent.parent / "acquisition-ledger.json"
        ).exists()

    print(
        {
            "status": "PASS",
            "acquisition_framework_version": ACQUISITION_FRAMEWORK_VERSION,
            "bounded_partial_resume": True,
            "raw_objects_sha256_backed": True,
            "complete_replay_no_fetch": True,
            "tamper_detection": True,
            "cursor_loop_fail_closed": True,
            "unledgered_source_drift_fail_closed": True,
            "auth_material_serialized": False,
            "network_used": False,
            "database_writes": False,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
