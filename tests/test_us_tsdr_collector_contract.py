from __future__ import annotations

from pathlib import Path

from app.us_tsdr.collector_contract import (
    COLLECTOR_CONTRACT_VERSION,
    collector_task_lines,
    parse_collector_csv,
    status_view_url,
)


def test_collector_task_lines_are_plain_tsdr_urls() -> None:
    assert COLLECTOR_CONTRACT_VERSION == "US_TSDR_COLLECTOR_TXT_CSV_V1"
    assert collector_task_lines(["90817045", "99000001"]) == [
        "https://tsdr.uspto.gov/statusview/sn90817045",
        "https://tsdr.uspto.gov/statusview/sn99000001",
    ]
    assert status_view_url("90817045") == "https://tsdr.uspto.gov/statusview/sn90817045"


def test_parse_label_value_collector_csv_preserves_correspondent_block(tmp_path: Path) -> None:
    path = tmp_path / "90817045.csv"
    path.write_text(
        """Attorney Name:Adriano Pacifici
Docket Number:00989
Attorney Primary Email Address:apacifici@iplawconsulting.com
Attorney Email Authorized:Yes

Correspondent Name/Address:
ADRIANO PACIFICI
INTELLECTUAL PROPERTY CONSULTING, LLC
400 POYDRAS STREET
SUITE 1400
NEW ORLEANS, LOUISIANA UNITED STATES 70130
Phone:504-323-6600
Correspondent e-mail:apacifici@iplawconsulting.com dmintlsz@yeah.net creid@iplawconsulting.com
Correspondent e-mail Authorized:Yes
""",
        encoding="utf-8",
    )

    observations = parse_collector_csv(path)
    assert len(observations) == 1
    item = observations[0]
    assert item.serial_number == "90817045"
    assert item.attorney_name == "Adriano Pacifici"
    assert item.docket_number == "00989"
    assert item.attorney_primary_email == "apacifici@iplawconsulting.com"
    assert item.attorney_email_authorized is True
    assert item.phone == "504-323-6600"
    assert item.correspondent_emails == (
        "apacifici@iplawconsulting.com",
        "dmintlsz@yeah.net",
        "creid@iplawconsulting.com",
    )
    assert item.correspondent_email_authorized is True
    assert item.correspondent_name_address_lines == (
        "ADRIANO PACIFICI",
        "INTELLECTUAL PROPERTY CONSULTING, LLC",
        "400 POYDRAS STREET",
        "SUITE 1400",
        "NEW ORLEANS, LOUISIANA UNITED STATES 70130",
    )
    assert "INTELLECTUAL PROPERTY CONSULTING, LLC" in item.correspondent_name_address_raw


def test_parse_wide_collector_csv_supports_serial_and_multiline_address(tmp_path: Path) -> None:
    path = tmp_path / "collector.csv"
    path.write_text(
        'Serial Number,Attorney Name,Attorney Primary Email Address,Correspondent Name/Address,'
        'Phone,Correspondent e-mail\n'
        '90817045,Adriano Pacifici,apacifici@iplawconsulting.com,"ADRIANO PACIFICI\n'
        '400 POYDRAS STREET",504-323-6600,"apacifici@iplawconsulting.com dmintlsz@yeah.net"\n',
        encoding="utf-8",
    )
    item = parse_collector_csv(path)[0]
    assert item.serial_number == "90817045"
    assert item.correspondent_name_address_lines == (
        "ADRIANO PACIFICI",
        "400 POYDRAS STREET",
    )
    assert item.correspondent_emails == (
        "apacifici@iplawconsulting.com",
        "dmintlsz@yeah.net",
    )
