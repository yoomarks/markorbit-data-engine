from __future__ import annotations

from pathlib import Path

import pytest
from psycopg.errors import LockNotAvailable

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.planner import build_plan
from app.contact_ingest import repository


def _write_csv(tmp_path: Path, name: str, text: str, *, encoding: str = "utf-8") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding=encoding)
    return path


def test_chinese_registered_agent_headers_keep_firm_and_person_distinct(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "agents.csv",
        "代理名称,代理地址,联系人,手机号\n"
        "北京示例知识产权代理有限公司,北京市海淀区,张三,13800138000\n",
    )
    plan = build_plan(path)
    table = plan.tables[0]
    entity = table.entities[0]

    assert plan.version == "CONTACT_INGEST_V1.6"
    assert table.profile == "AGENT_CONTACT_LIST"
    assert entity.canonical_name == "北京示例知识产权代理有限公司"
    assert entity.people[0].full_name == "张三"
    assert len(entity.people[0].channels) == 1
    assert entity.people[0].channels[0].owner_scope == "PERSON"


def test_india_agent_export_maps_agent_email_and_city(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "india.csv",
        "Agent_Number,Agent_Name,Agent_Address,Agent_City,Agent_Email,Agent_Last_Date\n"
        "1001,Example IP Associates,12 Example Road,Delhi,office@example.test,2026-01-01\n",
    )
    plan = build_plan(path)
    entity = plan.tables[0].entities[0]

    assert entity.canonical_name == "Example IP Associates"
    assert entity.city == "Delhi"
    assert any(channel.normalized_value == "office@example.test" for channel in entity.channels + entity.people[0].channels)


def test_chinese_us_agent_export_maps_person_channels(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "us-agent.csv",
        "代理人,代理人邮箱,代理人手机,代理人备用邮箱\n"
        "Jane Example,jane@example.test,+1 202 555 0100,jane.backup@example.test\n",
    )
    plan = build_plan(path)
    entity = plan.tables[0].entities[0]
    channels = entity.channels + [channel for person in entity.people for channel in person.channels]

    assert entity.canonical_name == "Jane Example"
    assert {channel.normalized_value for channel in channels} >= {
        "jane@example.test",
        "jane.backup@example.test",
    }


def test_legacy_qcc_source_name_requires_stable_identifier(tmp_path: Path) -> None:
    good = _write_csv(
        tmp_path,
        "qcc-good.csv",
        "原文件导入名称,登记状态,法定代表人,统一社会信用代码,电话\n"
        "深圳示例科技有限公司,存续,张三,91440300TEST000001,0755-12345678\n",
    )
    plan = build_plan(good)
    entity = plan.tables[0].entities[0]
    assert plan.tables[0].profile == "QCC_COMPANY_EXPORT"
    assert entity.canonical_name == "深圳示例科技有限公司"
    assert entity.identifiers["CN_USCC"] == "91440300TEST000001"

    bad = _write_csv(
        tmp_path,
        "qcc-bad.csv",
        "原文件导入名称,登记状态,法定代表人,统一社会信用代码,电话\n"
        "无法确认主体,存续,张三,,0755-12345678\n",
    )
    with pytest.raises(ValueError, match="no ingestible data rows"):
        build_plan(bad)


def test_ownerless_case_contact_is_preserved_without_inventing_owner(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "us-applicants.csv",
        "申请号,注册号,代理人,代理人邮箱,代理人手机,代理人备用邮箱\n"
        "98123456,7654321,,case@example.test,+1 202 555 0101,backup@example.test\n",
    )
    plan = build_plan(path)
    table = plan.tables[0]
    summary = plan.summary()

    assert table.profile == "CASE_CONTACT_TABLE"
    assert summary["entities_planned"] == 0
    assert summary["case_contacts_planned"] == 1
    assert summary["unresolved_channels_planned"] == 3
    record = table.case_contacts[0]
    assert record.application_number == "98123456"
    assert all(channel.owner_scope == "UNRESOLVED" for channel in record.channels)


def test_header_only_agent_sheet_is_invalid_instead_of_zero_row_ready(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "empty.csv",
        "入库时间,代理人,代理人邮箱,代理人备用邮箱,代理人地址,代理人电话\n",
    )
    with pytest.raises(ValueError, match="no ingestible data rows"):
        build_plan(path)


def test_headerless_email_name_register_is_inferred_conservatively(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "morocco.csv",
        "alpha@example.test,Alpha Example\n"
        "beta@example.test,Beta Example\n"
        "gamma@example.test,Gamma Example\n",
    )
    plan = build_plan(path)
    summary = plan.summary()

    assert summary["entities_planned"] == 3
    assert summary["channels_planned"] == 3
    assert plan.tables[0].header_row == 0
    assert any("Headerless legacy table inferred" in warning for warning in plan.tables[0].warnings)


def test_mongolia_contact_continuation_row_is_retained(tmp_path: Path) -> None:
    path = _write_csv(
        tmp_path,
        "mongolia.csv",
        "Д/д,Нэр,Мэргэжил,Тусгай зөвшөөрлийн дугаар,огноо,Холбоо барих\n"
        "1,Example Agent,Attorney,1001,2020-01-01,Утас: +976 99112233\n"
        ",,,,,Мэйл: agent@example.test\n",
    )
    plan = build_plan(path)
    entity = plan.tables[0].entities[0]
    channels = entity.channels + [channel for person in entity.people for channel in person.channels]

    assert {channel.normalized_value for channel in channels} >= {
        "+97699112233",
        "agent@example.test",
    }


def test_transient_lock_failure_retries_whole_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}

    def fake_apply(_plan):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise LockNotAvailable("lock timeout")
        return {"status": "SUCCESS"}

    monkeypatch.setattr(repository, "_apply_transaction", fake_apply)
    monkeypatch.setattr(repository.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(repository, "_record_failure", lambda *_args, **_kwargs: None)

    assert repository._apply_with_retry(object()) == {"status": "SUCCESS"}
    assert attempts["count"] == 3


def test_contact_ingestion_version_bumped() -> None:
    assert CONTACT_INGEST_VERSION == "CONTACT_INGEST_V1.6"
