from __future__ import annotations

import io
from pathlib import Path
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest import readers
from app.contact_ingest.planner import build_plan
from app.contact_ingest.task_queue import SUPPORTED_CONTACT_SUFFIXES


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _pdf_bytes(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 12 Tf", "72 720 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -18 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({escaped}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(out)


def _docx_bytes(rows: list[list[str]]) -> bytes:
    xml_rows: list[str] = []
    for row in rows:
        cells = []
        for value in row:
            escaped = (
                value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            cells.append(f"<w:tc><w:p><w:r><w:t>{escaped}</w:t></w:r></w:p></w:tc>")
        xml_rows.append(f"<w:tr>{''.join(cells)}</w:tr>")
    document = (
        f'<w:document xmlns:w="{WORD_NS}"><w:body><w:tbl>'
        f"{''.join(xml_rows)}"
        "</w:tbl></w:body></w:document>"
    )
    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", document)
    return out.getvalue()


def _assert_single_entity(path: Path, expected_name: str, expected_email: str) -> None:
    plan = build_plan(path)
    assert plan.version == CONTACT_INGEST_VERSION == "CONTACT_INGEST_V1.4"
    entity = plan.tables[0].entities[0]
    assert entity.canonical_name == expected_name
    channels = entity.channels + [channel for person in entity.people for channel in person.channels]
    assert any(channel.normalized_value == expected_email for channel in channels)


def test_txt_supports_delimited_contact_tables(tmp_path: Path) -> None:
    path = tmp_path / "contacts.txt"
    path.write_text(
        "Company|Email|Phone\nExample TXT Ltd|hello@example.test|+1 212 555 0100\n",
        encoding="utf-8",
    )
    _assert_single_entity(path, "Example TXT Ltd", "hello@example.test")


def test_txt_supports_key_value_contact_cards(tmp_path: Path) -> None:
    path = tmp_path / "contact-card.txt"
    path.write_text(
        "Company: Example Card Ltd\nEmail: card@example.test\nPhone: +44 20 7946 0958\n",
        encoding="utf-8",
    )
    _assert_single_entity(path, "Example Card Ltd", "card@example.test")


def test_html_supports_native_tables(tmp_path: Path) -> None:
    path = tmp_path / "contacts.html"
    path.write_text(
        "<html><body><table>"
        "<tr><th>Company</th><th>Email</th><th>Website</th></tr>"
        "<tr><td>Example HTML Ltd</td><td>html@example.test</td><td>example.test</td></tr>"
        "</table></body></html>",
        encoding="utf-8",
    )
    _assert_single_entity(path, "Example HTML Ltd", "html@example.test")


def test_docx_supports_word_tables(tmp_path: Path) -> None:
    path = tmp_path / "contacts.docx"
    path.write_bytes(
        _docx_bytes([
            ["Company", "Email", "Country"],
            ["Example Word Ltd", "word@example.test", "GB"],
        ])
    )
    _assert_single_entity(path, "Example Word Ltd", "word@example.test")


def test_pdf_supports_extractable_text_contact_tables(tmp_path: Path) -> None:
    path = tmp_path / "contacts.pdf"
    path.write_bytes(
        _pdf_bytes([
            "Company,Email,Phone",
            "Example PDF Ltd,pdf@example.test,+1 202 555 0100",
        ])
    )
    _assert_single_entity(path, "Example PDF Ltd", "pdf@example.test")


def test_legacy_doc_uses_antiword_text_extraction(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "contacts.doc"
    path.write_bytes(b"legacy-word-placeholder")
    monkeypatch.setattr(readers.shutil, "which", lambda name: "/usr/bin/antiword")

    def fake_run(*args, **kwargs):
        del args, kwargs
        return subprocess.CompletedProcess(
            args=["antiword"],
            returncode=0,
            stdout=b"Company\tEmail\nExample DOC Ltd\tdoc@example.test\n",
            stderr=b"",
        )

    monkeypatch.setattr(readers.subprocess, "run", fake_run)
    _assert_single_entity(path, "Example DOC Ltd", "doc@example.test")


def test_task_discovery_advertises_all_document_suffixes() -> None:
    assert {".pdf", ".txt", ".html", ".htm", ".docx", ".doc", ".xls"} <= SUPPORTED_CONTACT_SUFFIXES
