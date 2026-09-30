"""Historical GB stock ZIP v2 tests: UTF-16, co-owners and malformed quarantine."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from app.global_trademarks.gb_stock_snapshot_v2 import (
    CLASS_COLUMNS,
    GBStockError,
    SPECS,
    sha_file,
    stage_snapshot,
)


def source_row(spec, mark, owner, *, classes=(1,), status="Registered"):
    result = {k: "" for k in spec.base_columns + CLASS_COLUMNS}
    result.update(
        {
            "Trade Mark": mark,
            "Name": owner,
            "Status": status,
            "Mark Text": "BRAND",
            "Hyperlink": '=HYPERLINK("https://example.test","brand")',
        }
    )
    for number in classes:
        result[f"Class{number}"] = "1"
    return result


def make_zip(path, spec, rows, *, header=None, utf16=True, extra=False):
    names = header or list(spec.base_columns + CLASS_COLUMNS)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter="|", lineterminator="\n")
    writer.writerow(names)
    for item in rows:
        writer.writerow([item.get(name, "") for name in names] if isinstance(item, dict) else item)
    raw = buffer.getvalue().encode("utf-16" if utf16 else "utf-8")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(spec.member, raw)
        if extra:
            archive.writestr("unexpected.csv", "unexpected")


class GBStockSnapshotV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def prepare(
        self,
        stream,
        rows,
        *,
        header=None,
        utf16=True,
        extra=False,
        expected_bad=0,
        expected_unique=1,
        expected_duplicate=0,
    ):
        spec = SPECS[stream]
        path = self.root / spec.zip_name
        make_zip(path, spec, rows, header=header, utf16=utf16, extra=extra)
        spec = replace(
            spec,
            source_sha256=sha_file(path),
            expected_rows=len(rows),
            expected_quarantine=expected_bad,
            expected_unique_marks=expected_unique,
            expected_duplicate_marks=expected_duplicate,
        )
        return path, spec

    def test_domestic_coowners_are_distinct_source_rows_not_overwritten(self):
        base = SPECS["DOMESTIC"]
        first = source_row(base, "UK00000000001", "First owner", classes=(1, 9))
        second = source_row(base, "UK00000000001", "Second owner", classes=(25,))
        path, spec = self.prepare(
            "DOMESTIC",
            [first, second, ["bad-width"]],
            expected_bad=1,
            expected_unique=1,
            expected_duplicate=1,
        )
        out = self.root / "stage"
        dry = stage_snapshot(path, spec, out, expected_sha=spec.source_sha256)
        self.assertFalse(dry["output_written"])
        self.assertEqual(dry["accepted_source_rows"], 2)
        self.assertEqual(dry["quarantined_source_rows"], 1)
        self.assertFalse(out.exists())
        actual = stage_snapshot(path, spec, out, expected_sha=spec.source_sha256, write=True)
        self.assertTrue(actual["output_written"])
        self.assertFalse(actual["data_engine_ingested"])
        self.assertFalse(actual["current_state_verified"])
        self.assertEqual(actual["duplicate_mark_rows_preserved"], 1)
        rows = [
            json.loads(line)
            for line in Path(actual["rows_path"]).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([r["source_row_ordinal"] for r in rows], [1, 2])
        self.assertEqual([r["applicant_name_raw"] for r in rows], ["First owner", "Second owner"])
        self.assertEqual([r["nice_classes"] for r in rows], [[1, 9], [25]])
        self.assertEqual(rows[0]["application_number"], rows[1]["application_number"])
        self.assertEqual(
            rows[0]["source_fields"]["Hyperlink"], '=HYPERLINK("https://example.test","brand")'
        )
        self.assertNotEqual(rows[0]["source_cells_sha256"], rows[1]["source_cells_sha256"])
        bad = [
            json.loads(line)
            for line in Path(actual["quarantine_path"]).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(bad[0]["source_row_ordinal"], 3)
        self.assertEqual(bad[0]["reason"], "FIELD_COUNT")
        self.assertEqual(bad[0]["cells"], ["bad-width"])
        self.assertEqual(sha_file(Path(actual["manifest_path"])), actual["manifest_sha256"])
        with self.assertRaisesRegex(GBStockError, "already exists"):
            stage_snapshot(path, spec, out, expected_sha=spec.source_sha256, write=True)

    def test_domestic_letter_suffix_preserves_distinct_marks_and_coowners(self):
        base = SPECS["DOMESTIC"]
        rows = [
            source_row(base, "UK0000000052A", "First A owner"),
            source_row(base, "UK0000000052B", "B owner"),
            source_row(base, "UK002198182AA", "Double-suffix owner"),
            source_row(base, "UK0000000052A", "Second A owner"),
        ]
        path, spec = self.prepare("DOMESTIC", rows, expected_unique=3, expected_duplicate=1)
        manifest = stage_snapshot(
            path,
            spec,
            self.root / "out",
            expected_sha=spec.source_sha256,
            write=True,
        )
        self.assertEqual(manifest["accepted_source_rows"], 4)
        self.assertEqual(manifest["duplicate_mark_rows_preserved"], 1)
        actual = [
            json.loads(line)
            for line in Path(manifest["rows_path"]).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [row["application_number"] for row in actual],
            ["UK0000000052A", "UK0000000052B", "UK002198182AA", "UK0000000052A"],
        )
        self.assertEqual(
            [row["applicant_name_raw"] for row in actual],
            ["First A owner", "B owner", "Double-suffix owner", "Second A owner"],
        )

    def test_madrid_letter_suffix_is_valid_but_unreviewed_id_fails_closed(self):
        base = SPECS["MADRID_IR"]
        records = [
            source_row(base, "WO000000142594B", "Owner B"),
            source_row(base, "WO000000142594", "Owner without suffix"),
            source_row(base, "WO000000142594AB", "Unreviewed suffix"),
        ]
        path, spec = self.prepare("MADRID_IR", records, expected_bad=1, expected_unique=2)
        manifest = stage_snapshot(
            path,
            spec,
            self.root / "out",
            expected_sha=spec.source_sha256,
            write=True,
        )
        self.assertEqual(manifest["accepted_source_rows"], 2)
        self.assertEqual(manifest["quarantined_source_rows"], 1)
        bad = json.loads(Path(manifest["quarantine_path"]).read_text(encoding="utf-8"))
        self.assertEqual(bad["reason"], "MARK_ID")

    def test_madrid_ir_has_separate_source_and_45_classes(self):
        base = SPECS["MADRID_IR"]
        record = source_row(
            base, "WO0000000130221", "Foreign owner", classes=(9, 45), status="Protected"
        )
        record["Date of Designation"] = "2017-05-25"
        path, spec = self.prepare("MADRID_IR", [record])
        manifest = stage_snapshot(
            path, spec, self.root / "out", expected_sha=spec.source_sha256, write=True
        )
        row = json.loads(Path(manifest["rows_path"]).read_text(encoding="utf-8"))
        self.assertEqual(row["source_stream"], "MADRID_IR")
        self.assertEqual(row["nice_classes"], [9, 45])
        self.assertEqual(row["source_fields"]["Date of Designation"], "2017-05-25")
        self.assertTrue(row["historical_source_only"])

    def test_exact_source_sha_utf16_and_header_are_frozen(self):
        base = SPECS["DOMESTIC"]
        row = source_row(base, "UK00000000001", "Owner")
        path, spec = self.prepare("DOMESTIC", [row])
        with self.assertRaisesRegex(GBStockError, "SHA drift"):
            stage_snapshot(path, spec, self.root / "x", expected_sha="f" * 64)
        path, spec = self.prepare("DOMESTIC", [row], utf16=False)
        with self.assertRaisesRegex(GBStockError, "UTF-16 LE"):
            stage_snapshot(path, spec, self.root / "x", expected_sha=spec.source_sha256)
        wrong = list(base.base_columns + CLASS_COLUMNS)
        wrong[1] = "Unreviewed changed column"
        path, spec = self.prepare("DOMESTIC", [row], header=wrong)
        with self.assertRaisesRegex(GBStockError, "header"):
            stage_snapshot(path, spec, self.root / "x", expected_sha=spec.source_sha256)
        path, spec = self.prepare("DOMESTIC", [row], extra=True)
        with self.assertRaisesRegex(GBStockError, "extra evidence"):
            stage_snapshot(path, spec, self.root / "x", expected_sha=spec.source_sha256)

    def test_invalid_class_flag_quarantined_not_silently_interpreted(self):
        base = SPECS["DOMESTIC"]
        row = source_row(base, "UK00000000001", "Owner", classes=())
        row["Class9"] = "unreviewed"
        path, spec = self.prepare("DOMESTIC", [row], expected_bad=1, expected_unique=0)
        result = stage_snapshot(
            path, spec, self.root / "x", expected_sha=spec.source_sha256, write=True
        )
        self.assertEqual(result["accepted_source_rows"], 0)
        bad = json.loads(Path(result["quarantine_path"]).read_text(encoding="utf-8"))
        self.assertEqual(bad["reason"], "CLASS_FLAG")
        self.assertEqual(bad["source_row_ordinal"], 1)

    def test_cardinality_drift_fails_closed_before_manifest(self):
        base = SPECS["DOMESTIC"]
        path, spec = self.prepare("DOMESTIC", [source_row(base, "UK00000000001", "Owner")])
        altered = replace(spec, expected_unique_marks=2)
        with self.assertRaisesRegex(GBStockError, "cardinality"):
            stage_snapshot(path, altered, self.root / "dry", expected_sha=altered.source_sha256)
        self.assertFalse((self.root / "dry").exists())


if __name__ == "__main__":
    unittest.main()
