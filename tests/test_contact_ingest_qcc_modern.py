from __future__ import annotations

import csv
from pathlib import Path

from app.contact_ingest import CONTACT_INGEST_VERSION
from app.contact_ingest.planner import build_plan


def test_modern_qcc_batch_export_is_ingestible(tmp_path: Path) -> None:
    path = tmp_path / "qcc-modern.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["企查查导出免责声明"])
        writer.writerow([
            "原文件导入名称",
            "系统匹配企业名称",
            "登记状态",
            "法定代表人",
            "成立日期",
            "统一社会信用代码",
            "企业地址",
            "所属省份",
            "所属城市",
            "电话",
            "更多电话",
            "邮箱",
            "更多邮箱",
            "曾用名",
            "英文名",
            "官网",
        ])
        writer.writerow([
            "北京睿博美佳",
            "北京睿博美佳商贸有限公司",
            "存续",
            "唐海艳",
            "2008-01-10",
            "91110102671739359M",
            "北京市怀柔区九渡河镇怀长路8号（集群注册）",
            "北京市",
            "北京市",
            "13683060405",
            "010-84475800;18511748018",
            "358872327@qq.com",
            "tara@villalifestyles.cn;xiaolin.liu@iqair-china.com",
            "",
            "Beijing Ruibo Meijia Business Co., Ltd.",
            "http://www.iqair-china.com",
        ])

    plan = build_plan(path)
    table = plan.tables[0]
    entity = table.entities[0]

    assert plan.version == CONTACT_INGEST_VERSION == "CONTACT_INGEST_V1.2"
    assert table.header_row == 2
    assert table.profile == "QCC_COMPANY_EXPORT"
    assert table.profile_confidence >= 0.9
    assert entity.canonical_name == "北京睿博美佳商贸有限公司"
    assert entity.country_code == "CN"
    assert entity.identifiers["CN_USCC"] == "91110102671739359M"
    assert entity.people[0].full_name == "唐海艳"
    assert entity.people[0].channels == []
    assert {channel.owner_scope for channel in entity.channels} == {"ENTITY"}
    assert any(channel.normalized_value == "tara@villalifestyles.cn" for channel in entity.channels)
    assert any(channel.normalized_value == "iqair-china.com" for channel in entity.channels)
