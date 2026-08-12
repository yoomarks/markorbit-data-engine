from __future__ import annotations

from pathlib import Path

import pytest

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.planner import build_plan


def _pdf_bytes(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 11 Tf", "54 744 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("0 -15 Td")
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


def _all_channels(plan) -> set[str]:
    values: set[str] = set()
    for table in plan.tables:
        for entity in table.entities:
            values.update(channel.normalized_value for channel in entity.channels)
            for person in entity.people:
                values.update(channel.normalized_value for channel in person.channels)
    return values


def test_v15_parses_berlin_style_narrative_attorney_pdf(tmp_path: Path) -> None:
    path = tmp_path / "agent_2025-January-Updated-List-of-Attorneys-Berlin.pdf"
    path.write_bytes(
        _pdf_bytes([
            "LIST OF ATTORNEYS",
            "UNITED STATES EMBASSY BERLIN",
            "Clayallee 170",
            "14191 Berlin",
            "Tel. +49 30 8305 0",
            "Email: embassy@example.test",
            "English-Speaking Attorneys in Berlin",
            "Ayla Kremen Adomat",
            "Adomat Immigration",
            "Schillerstrasse 10",
            "10625 Berlin",
            "Website: www.adomatimmigration.com",
            "E-Mail: ayla@example.test",
            "Telephone: +49 30 311 96203",
            "Ability to read/speak English: Native language",
            "Dr. David Albrecht",
            "FS-PP Berlin",
            "Potsdamer Platz 8",
            "10117 Berlin",
            "E-Mail: david@example.test",
            "Telephone: +49 30 318 6853",
            "Law background: Criminal law and corporations.",
        ])
    )

    plan = build_plan(path)
    summary = plan.summary()
    entities = plan.tables[0].entities
    people = {person.full_name for entity in entities for person in entity.people}

    assert CONTACT_INGEST_VERSION == "CONTACT_INGEST_V1.5"
    assert plan.tables[0].profile == "AGENT_CONTACT_LIST"
    assert summary["entities_planned"] == 2
    assert {entity.canonical_name for entity in entities} == {
        "Adomat Immigration",
        "FS-PP Berlin",
    }
    assert people == {"Ayla Kremen Adomat", "Dr. David Albrecht"}
    assert {"ayla@example.test", "david@example.test"} <= _all_channels(plan)
    assert "embassy@example.test" not in _all_channels(plan)


def test_v15_html_narrative_directory_beats_incidental_unusable_table(tmp_path: Path) -> None:
    path = tmp_path / "agent_directory.html"
    path.write_text(
        "<html><body>"
        "<table><tr><td>Navigation only</td></tr></table>"
        "<div>Jane Example</div><div>Example Legal</div><div>1 Main Street</div>"
        "<div>Email: jane@example.test</div><div>Telephone: +1 202 555 0100</div>"
        "<div>John Sample</div><div>Sample Law</div><div>2 Second Road</div>"
        "<div>Email: john@example.test</div><div>Telephone: +1 202 555 0101</div>"
        "</body></html>",
        encoding="utf-8",
    )

    plan = build_plan(path)
    assert plan.summary()["entities_planned"] == 2
    assert {"jane@example.test", "john@example.test"} <= _all_channels(plan)


def test_v15_does_not_promote_single_contact_prose_to_directory(tmp_path: Path) -> None:
    path = tmp_path / "ordinary-document.txt"
    path.write_text(
        "Jane Example\n"
        "1 Main Street\n"
        "Email: jane@example.test\n"
        "Telephone: +1 202 555 0100\n"
        "This document contains general legal information and is not a directory.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="No ingestible entity/contact tables"):
        build_plan(path)
