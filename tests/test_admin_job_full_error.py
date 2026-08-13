from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_task_center_exposes_full_failed_job_error() -> None:
    markup = (ROOT / "web" / "admin-jobs.html").read_text(encoding="utf-8")
    assert "查看完整错误" in markup
    assert "showFullError" in markup
    assert "white-space:pre-wrap" in markup
    assert "error_message" in markup
