from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.contact_ingest import CONTACT_INGEST_VERSION, CONTACT_SCHEMA_VERSION
from app.contact_ingest.migrations import SCHEMA_SQL
from app.contact_ingest.normalization import normalize_country_code, normalize_email, normalize_phone
from app.contact_ingest.planner import build_plan


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _col_name(index: int) -> str:
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def _xlsx_bytes(rows: list[list[str]], *, sheet_name: str = "Data") -> bytes:
    workbook = (
        f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
        f'<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        f'<Relationships xmlns="{PKG_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    sheet_rows = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for index, value in enumerate(row):
            ref = f"{_col_name(index)}{row_number}"
            escaped = (
                str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>')
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    sheet = f'<worksheet xmlns="{MAIN_NS}"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'

    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    return out.getvalue()


def test_qcc_xlsx_autodetects_header_and_keeps_company_channels_off_legal_rep(tmp_path: Path) -> None:
    rows = [
        ["本数据仅供测试，实际文件第一行可能为免责声明"],
        [
            "企业名称", "登记状态", "法定代表人", "成立日期", "统一社会信用代码",
            "注册地址", "所属省份", "所属城市", "有效手机号", "更多电话", "邮箱",
            "曾用名", "英文名", "官网网址",
        ],
        [
            "深圳示例电子科技有限公司", "存续", "张示例", "2020-01-02", "91440300TEST000001",
            "深圳市南山区示例路1号", "广东省", "深圳市", "13800138000", "0755-12345678;0755-87654321",
            "sales@example.test;service@example.test", "深圳示例科技有限公司", "Shenzhen Example Tech Ltd.",
            "https://www.example.test/contact",
        ],
    ]
    path = tmp_path / "qcc.xlsx"
    path.write_bytes(_xlsx_bytes(rows, sheet_name="企业数据"))

    plan = build_plan(path)
    summary = plan.summary()
    table = plan.tables[0]
    entity = table.entities[0]

    assert summary["version"] == CONTACT_INGEST_VERSION
    assert table.header_row == 2
    assert table.profile == "QCC_COMPANY_EXPORT"
    assert table.profile_confidence == 1.0
    assert entity.country_code == "CN"
    assert entity.identifiers["CN_USCC"] == "91440300TEST000001"
    assert len(entity.people) == 1
    assert entity.people[0].relation_type == "LEGAL_REPRESENTATIVE"
    assert entity.people[0].channels == []
    assert {item.owner_scope for item in entity.channels} == {"ENTITY"}
    assert any(item.normalized_value == "+8613800138000" for item in entity.channels)
    assert any(item.channel_type == "WEBSITE" and item.normalized_value == "example.test" for item in entity.channels)
    assert len(entity.channels) == 6


def test_agent_csv_attaches_person_channels_but_keeps_website_on_firm(tmp_path: Path) -> None:
    path = tmp_path / "agents.csv"
    path.write_text(
        "Firm Name,Contact Person,Title,Email,Phone,Website,Country\n"
        "Example IP LLP,Alex Example,Partner,alex@example.test,+1 202 555 0100,https://www.example.test,US\n",
        encoding="utf-8",
    )

    plan = build_plan(path)
    table = plan.tables[0]
    entity = table.entities[0]
    person = entity.people[0]

    assert table.profile == "AGENT_CONTACT_LIST"
    assert entity.country_code == "US"
    assert person.relation_type == "ATTORNEY"
    assert person.title == "Partner"
    assert {c.channel_type for c in person.channels} == {"EMAIL", "PHONE_UNKNOWN"}
    assert all(c.owner_scope == "PERSON" for c in person.channels)
    assert len(entity.channels) == 1
    assert entity.channels[0].channel_type == "WEBSITE"
    assert entity.channels[0].owner_scope == "ENTITY"


def test_generic_json_with_cleaned_header_keys_is_supported(tmp_path: Path) -> None:
    path = tmp_path / "contacts.json"
    path.write_text(
        json.dumps([
            {" Company Name ": "Example Manufacturing Co.", "Email": "INFO@EXAMPLE.TEST", "Country": "United States"}
        ]),
        encoding="utf-8",
    )
    plan = build_plan(path)
    entity = plan.tables[0].entities[0]
    assert plan.tables[0].profile == "GENERIC_CONTACT_TABLE"
    assert entity.canonical_name == "Example Manufacturing Co."
    assert entity.country_code == "US"
    assert entity.channels[0].normalized_value == "info@example.test"


def test_zip_can_mix_supported_structured_contact_files(tmp_path: Path) -> None:
    csv_data = b"Company,Email\nAlpha Example Ltd.,alpha@example.test\n"
    json_data = json.dumps([{"Company": "Beta Example Ltd.", "Phone": "+44 20 7946 0958"}]).encode()
    path = tmp_path / "mixed.zip"
    with ZipFile(path, "w", ZIP_DEFLATED) as zf:
        zf.writestr("alpha.csv", csv_data)
        zf.writestr("nested/beta.json", json_data)

    plan = build_plan(path)
    assert len(plan.tables) == 2
    assert {e.canonical_name for t in plan.tables for e in t.entities} == {
        "Alpha Example Ltd.", "Beta Example Ltd."
    }


def test_invalid_legacy_xls_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "legacy.xls"
    path.write_bytes(b"not-an-xls")
    with pytest.raises(ValueError, match="Invalid or unreadable XLS file"):
        build_plan(path)


def test_normalization_is_deterministic() -> None:
    assert normalize_email(" ABC@Example.TEST ") == "abc@example.test"
    assert normalize_phone("138-0013-8000", "CN") == "+8613800138000"
    assert normalize_country_code("United States") == "US"
    assert normalize_country_code("中国") == "CN"


def test_contact_schema_keeps_person_channel_observation_and_marketing_view_separate() -> None:
    for required in (
        "CREATE TABLE IF NOT EXISTS entity.entity_identifier",
        "CREATE TABLE IF NOT EXISTS contact.person",
        "CREATE TABLE IF NOT EXISTS contact.entity_person_relation",
        "CREATE TABLE IF NOT EXISTS contact.channel",
        "CREATE TABLE IF NOT EXISTS contact.channel_observation",
        "CREATE TABLE IF NOT EXISTS contact.raw_record",
        "CREATE OR REPLACE VIEW contact.v_marketing_contacts",
        "CHECK ((entity_id IS NOT NULL)::int + (person_id IS NOT NULL)::int = 1)",
    ):
        assert required in SCHEMA_SQL
    assert CONTACT_SCHEMA_VERSION == "CONTACT_SCHEMA_V1.1"


def test_repository_never_reassigns_existing_trademark_mentions() -> None:
    entity_store = (Path(__file__).parents[1] / "app" / "contact_ingest" / "entity_store.py").read_text(encoding="utf-8")
    assert "WHERE entity_id IS NULL" in entity_store
    assert 'method == "CONTACT_SOURCE_NEW_AMBIGUOUS"' in entity_store


def test_operator_wrapper_is_dry_run_by_default_and_never_starts_persistent_worker() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "import-contacts.ps1").read_text(encoding="utf-8")
    assert "[switch]$Apply" in script
    assert '"--no-deps"' in script
    assert '$argsList += "--apply"' in script
    assert "docker compose up" not in script.lower()
