from __future__ import annotations

import json
from pathlib import Path

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.directory_text_v16 import directory_contact_text_table
from app.contact_ingest.html_directory import html_directory_contact_table
from app.contact_ingest.models import TableData
from app.contact_ingest.planner import build_plan
from app.contact_ingest.special_formats import adapt_contact_tables
from app.contact_ingest.task_queue import SUPPORTED_CONTACT_SUFFIXES


ROOT = Path(__file__).resolve().parents[1]


def _channels(plan) -> set[str]:
    values: set[str] = set()
    for table in plan.tables:
        for entity in table.entities:
            values.update(channel.normalized_value for channel in entity.channels)
            for person in entity.people:
                values.update(channel.normalized_value for channel in person.channels)
    return values


def test_v16_parses_epo_and_belarus_style_html_cards(tmp_path: Path) -> None:
    path = tmp_path / "epo.html"
    path.write_text(
        "<html><body>"
        "<section><h3>Abbaszadeh Banaeiyan, Amin</h3><span>9302250</span>"
        "<div>Lind Edlund Kenamets Intellectual Property AB</div>"
        "<a href='tel:+46763041831'>+46 76 304 18 31</a>"
        "<a href='mailto:amin@example.test'>amin@example.test</a></section>"
        "<section><h3>Example, Bea</h3><span>9302251</span>"
        "<div>Example Patent AB</div>"
        "<a href='tel:+46763041832'>+46 76 304 18 32</a>"
        "<a href='mailto:bea@example.test'>bea@example.test</a></section>"
        "</body></html>",
        encoding="utf-8",
    )

    plan = build_plan(path)
    assert CONTACT_INGEST_VERSION == "CONTACT_INGEST_V1.6"
    assert plan.summary()["entities_planned"] == 2
    assert {"amin@example.test", "bea@example.test"} <= _channels(plan)
    assert {entity.identifiers.get("AGENT_CODE") for entity in plan.tables[0].entities} == {
        "9302250",
        "9302251",
    }


def test_v16_parses_oapi_firm_blocks() -> None:
    html = (
        "<table><tr><td><strong>CABINET OGUE Basile</strong></td></tr>"
        "<tr><td>Tel: +229 95 79 83 38<br>"
        "E-mail: <a href='mailto:ogue@example.test'>ogue@example.test</a></td></tr></table>"
        "<table><tr><td><strong>Cabinet AHONAKO Houmenou Maxime</strong></td></tr>"
        "<tr><td>Tel: +229 95 64 64 40<br>"
        "E-mail: <a href='mailto:maxime@example.test'>maxime@example.test</a></td></tr></table>"
    )
    table = html_directory_contact_table(html, source_member="oapi.html")
    assert table is not None
    assert len(table.rows) == 3
    assert table.rows[1][1] == "CABINET OGUE Basile"
    assert "ogue@example.test" in table.rows[1][3]


def test_v16_accepts_historical_josn_export(tmp_path: Path) -> None:
    path = tmp_path / "agents.josn"
    path.write_text(
        json.dumps([
            {
                "AGENT NO.": "1",
                "SURNAME": "Hime",
                "OTHER NAMES": "Peter Julian",
                "COMPANY": "Kaplan & Stratton Advocates",
                "BUSINESS PHONE": "+254 20 2841000",
                "EMAIL": "peter@example.test",
                "STATUS": "Active",
            }
        ]),
        encoding="utf-8",
    )
    plan = build_plan(path)
    entity = plan.tables[0].entities[0]
    assert entity.canonical_name == "Kaplan & Stratton Advocates"
    assert entity.identifiers["AGENT_CODE"] == "1"
    assert "peter@example.test" in _channels(plan)
    assert ".josn" in SUPPORTED_CONTACT_SUFFIXES


def test_v16_maps_uspto_practitioner_registration_number(tmp_path: Path) -> None:
    path = tmp_path / "us-practitioner.txt"
    path.write_text(
        "Last name,First name,Middle name,Suffix,Firm name,Job tile,Address,City,State,Country,Phone number,Registration number,Status\n"
        "Aaron,John,R,,Dowling Aaron Incorporated,,8080 N. Palm Avenue,Fresno,CA,US,559-432-4500,76269,AGENT\n",
        encoding="utf-8",
    )
    entity = build_plan(path).tables[0].entities[0]
    assert entity.canonical_name == "Dowling Aaron Incorporated"
    assert entity.identifiers["AGENT_CODE"] == "76269"
    assert entity.people[0].full_name == "John R Aaron"


def test_v16_maps_chinese_legacy_agent_address(tmp_path: Path) -> None:
    path = tmp_path / "cn-agent.csv"
    path.write_text(
        "代理名称,代理地址,联系人,手机号\n"
        "北京示例知识产权代理有限公司,北京市海淀区,张三,13800138000\n",
        encoding="utf-8",
    )
    entity = build_plan(path).tables[0].entities[0]
    assert entity.normalized_address
    assert entity.people[0].full_name == "张三"


def test_v16_adapts_headerless_aripo_vertical_directory() -> None:
    table = TableData(
        source_member="aripo.xls",
        sheet_name="Agents",
        rows=[
            ["Esther A Rije", "Morara Apiemi & Nyangito Advocates"],
            ["", "Nairobi | Kenya"],
            ["", "Tel: +254 20 1234567"],
            ["", "Email/s: esther@example.test"],
            ["", "Website: www.example.test"],
            ["Equitas Attorneys", "P.O. Box 123 Nairobi"],
            ["", "Tel: +254 20 7654321"],
            ["", "Email/s: info@equitas.example"],
            ["", "Website: www.equitas.example"],
        ],
    )
    adapted = adapt_contact_tables([table])[0]
    assert adapted.sheet_name.endswith("vertical-directory")
    assert len(adapted.rows) == 3
    assert adapted.rows[1][0] == "Esther A Rije"
    assert adapted.rows[2][1] == "Equitas Attorneys"


def test_v16_adapts_singapore_foreign_agent_table() -> None:
    table = TableData(
        source_member="singapore.pdf",
        sheet_name="page-1-table-1",
        rows=[
            [
                "Registration No.",
                "Name of Foreign Patents Agent",
                "Date of Registration",
                "Status of Practising Certificate",
                "Contact Address",
            ],
            [
                "FPA/1706/004",
                "Ms Khandelwal Barkha",
                "21-Sep-17",
                "In Force",
                "Morgan Lewis Stamford LLC 10 Collyer Quay Singapore 049315 "
                "Tel: 6389 3079 Email: Barkha.khandelwal@example.test",
            ],
        ],
    )
    adapted = adapt_contact_tables([table])[0]
    assert adapted.sheet_name.endswith("foreign-agent")
    assert adapted.rows[1][0] == "Ms Khandelwal Barkha"
    assert adapted.rows[1][6] == "FPA/1706/004"
    assert adapted.rows[1][3] == "Barkha.khandelwal@example.test"


def test_v16_adapts_mozambique_aopi_table() -> None:
    table = TableData(
        source_member="mozambique.pdf",
        sheet_name="page-1-table-1",
        rows=[
            ["Nº Do AOPI", "Nome", "Escritório", "Endereço", "Cidade", "Telefone", "Fax", "Telemóvel", "Email", "Website"],
            ["03", "Delfim De Deus Júnior", "DDJ Law Online Lda", "Av. Samora Machel 30", "Maputo", "+258-21-439733", "", "+258-84-3100000", "delfim@example.test", "www.example.test"],
        ],
    )
    adapted = adapt_contact_tables([table])[0]
    assert adapted.sheet_name.endswith("aopi")
    assert adapted.rows[1][0] == "Delfim De Deus Júnior"
    assert adapted.rows[1][-1] == "03"


def test_v16_inline_directory_rejects_dates_as_phones() -> None:
    table = directory_contact_text_table(
        "Francesco CHIANINI – Via Fulvio Croce 13, 52100 Arezzo. "
        "Tel/Fax: 0575/355201. Cell: 338/3520366. E-mail: francesco@example.test. "
        "Born in 1969.\n"
        "Stephen EUFRATE – Via Michelangelo Buonarroti 16, 54033 Carrara. "
        "Tel/Fax: 0585/73606. Cell: 339/1945290. E-mail: stephen@example.test. "
        "Registration date 24.7.2025.",
        source_member="italy.pdf",
    )
    assert table is not None
    assert len(table.rows) == 3
    assert "24.7.2025" not in table.rows[2][4]


def test_v16_scanned_pdf_runtime_contract() -> None:
    dockerfile = (ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    readers = (ROOT / "app" / "contact_ingest" / "readers.py").read_text(encoding="utf-8")
    assert "tesseract-ocr" in dockerfile
    assert 'shutil.which("tesseract")' in readers
    assert "resolution=220" in readers
    assert "_OCR_MAX_PAGES = 40" in readers
