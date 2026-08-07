from pathlib import Path
import re
import sys
import types
import uuid

# This contract test only exercises pure row builders; database clients are stubbed.
db_stub = types.ModuleType("app.db")
db_stub.clickhouse_client = lambda: None
db_stub.postgres_conn = lambda: None
sys.modules.setdefault("app.db", db_stub)

from app.cn.ingest import (
    STAGE_COLUMNS,
    _basic_stage_row,
    _other_stage_row,
    _party_values,
)


def _top_level_select_count(query: str) -> int:
    match = re.search(r"\bSELECT\b", query, flags=re.I)
    assert match
    start = match.end()
    depth = 0
    quote = None
    end = None
    i = start
    while i < len(query):
        char = query[i]
        if quote:
            if char == quote and (i == 0 or query[i - 1] != "\\"):
                quote = None
        else:
            if char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif depth == 0 and query[i : i + 4].upper() == "FROM":
                before = query[i - 1] if i else " "
                after = query[i + 4] if i + 4 < len(query) else " "
                if not (before.isalnum() or before == "_") and not (
                    after.isalnum() or after == "_"
                ):
                    end = i
                    break
        i += 1
    if end is None and "{common_join}" in query:
        end = query.index("{common_join}")
    assert end is not None
    select_list = query[start:end]
    depth = 0
    quote = None
    count = 1
    for index, char in enumerate(select_list):
        if quote:
            if char == quote and (index == 0 or select_list[index - 1] != "\\"):
                quote = None
        else:
            if char in {"'", '"'}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                count += 1
    return count


def _schema_column_counts() -> dict[str, int]:
    sql = Path("database/clickhouse/init/001_fact_schema.sql").read_text(
        encoding="utf-8"
    )
    counts: dict[str, int] = {}
    for match in re.finditer(
        r"CREATE TABLE IF NOT EXISTS\s+([\w.]+)\s*\((.*?)\)\s*ENGINE",
        sql,
        flags=re.I | re.S,
    ):
        table = match.group(1)
        body = match.group(2)
        columns = []
        for raw_line in body.splitlines():
            line = raw_line.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            columns.append(line.split()[0])
        counts[table] = len(columns)
    return counts


def test_all_publish_insert_selects_match_target_column_count():
    source = Path("app/cn/ingest.py").read_text(encoding="utf-8")
    schema_counts = _schema_column_counts()
    inserts = re.findall(r'client\.command\(f"""(.*?)"""\)', source, flags=re.S)
    checked = 0
    for query in inserts:
        target = re.search(r"INSERT\s+INTO\s+([\w.]+)", query, flags=re.I)
        if not target:
            continue
        table = target.group(1)
        assert table in schema_counts
        select_query = query[target.end() :]
        assert _top_level_select_count(select_query) == schema_counts[table], table
        checked += 1
    assert checked >= 15


def test_stage_builders_match_stage_schema():
    package_id = uuid.uuid4()
    basic_record = {
        "application_number": "G602365A",
        "class_no": "9",
        "filing_date": "1950-01-01",
        "mark_name": "测试商标",
        "agent_code": "100001",
        "prelim_pub_date": "1951-01-01",
        "registration_pub_date": "1951-02-01",
        "exclusive_start_date": "1951-03-01",
        "exclusive_end_date": "1961-03-01",
    }
    basic_row, _, _ = _basic_stage_row(package_id, basic_record, "basic.csv", 2, 2)
    assert basic_row is not None
    assert len(basic_row) == len(STAGE_COLUMNS["markorbit_facts.cn_stage_basic"])

    owner_record = {
        "application_number": "G602365A",
        "class_no": "9",
        "owner_name_cn": "测试有限公司",
        "owner_address_cn": "北京市朝阳区测试路1号",
    }
    owner = _party_values(package_id, owner_record, "OWNER", "owner.csv", 2, 2)
    assert owner is not None
    assert len(owner.row) == len(STAGE_COLUMNS[owner.table])

    role_records = {
        "goods": {
            "application_number": "G602365A",
            "class_no": "9",
            "similar_group": "0901",
            "goods_sequence": "1",
            "goods_name": "计算机",
            "goods_status_raw": "2",
        },
        "priority": {
            "application_number": "G602365A",
            "class_no": "9",
            "priority_number": "P1",
        },
        "madrid": {
            "application_number": "G602365A",
            "international_registration_number": "602365",
        },
        "agent": {"agent_code": "100001", "agent_name": "测试代理有限公司"},
    }
    for role, record in role_records.items():
        staged = _other_stage_row(role, package_id, record, f"{role}.csv", 2, 2)
        assert staged is not None
        table, row, _, _ = staged
        assert len(row) == len(STAGE_COLUMNS[table]), role
