from app.snapshot_delta.loader import SnapshotCsvLoader


def test_loader_accepts_authoritative_fields_larger_than_default_csv_limit(tmp_path):
    snapshot = tmp_path / "large-field.csv"
    large_value = "x" * 200_000
    snapshot.write_text(
        'Application Number,Mark Status,Mark Data\nSG1,Pending,"'
        + large_value
        + '"\n',
        encoding="utf-8",
    )

    loader = SnapshotCsvLoader(snapshot)
    rows = list(loader.rows())

    assert loader.fieldnames() == ("Application Number", "Mark Status", "Mark Data")
    assert loader.count() == 1
    assert rows[0]["Application Number"] == "SG1"
    assert rows[0]["Mark Data"] == large_value
