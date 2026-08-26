from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import time
import zipfile

from app.cn.ingest import _cleanup_partial_outputs, ingest_cn_package
from app.cn.validate_fixture import (
    AGENT_CODE,
    DIRECT_APP,
    G_CHILD,
    G_ROOT,
    MULTI_APP,
    _assert_fixture,
)
from app.domain import DiscoveredPackage
from app.repository import get_package, register_package


FIXTURE_VERSION = "CN_BOUNDED_REAL_ZIP_E2E_V1"


def _csv_bytes(headers: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, members[name])


def _base_members() -> dict[str, bytes]:
    basic_headers = [
        "注册号/申请号",
        "国际分类",
        "申请日期",
        "商标名称",
        "商标类型",
        "代理机构代码",
        "初审公告期号",
        "初审公告日期",
        "注册公告期号",
        "注册公告日期",
        "专用期开始日期",
        "专用期结束日期",
        "专用有效期",
    ]
    basic_rows = [
        [DIRECT_APP, "25", "2020-01-02", "FIXTURE DIRECT", "WORD", AGENT_CODE, "1801", "2020-05-01", "1810", "2020-07-01", "2020-07-01", "2030-06-30", "2020-07-01至2030-06-30"],
        [MULTI_APP, "9", "2020-01-02", "FIXTURE MULTI", "WORD", AGENT_CODE, "1801", "2020-05-01", "1810", "2020-07-01", "2020-07-01", "2030-06-30", "2020-07-01至2030-06-30"],
        [MULTI_APP, "42", "2020-01-02", "FIXTURE MULTI", "WORD", AGENT_CODE, "1801", "2020-05-01", "1810", "2020-07-01", "2020-07-01", "2030-06-30", "2020-07-01至2030-06-30"],
        [G_ROOT, "25", "2020-01-02", "FIXTURE MADRID ROOT", "WORD", AGENT_CODE, "1801", "2020-05-01", "1810", "2020-07-01", "2020-07-01", "2030-06-30", "2020-07-01至2030-06-30"],
        [G_CHILD, "25", "2020-01-02", "FIXTURE MADRID CHILD", "WORD", AGENT_CODE, "1801", "2020-05-01", "1810", "2020-07-01", "2020-07-01", "2030-06-30", "2020-07-01至2030-06-30"],
    ]
    applicant_headers = ["注册号/申请号", "国际分类", "注册人中文名称", "注册人中文地址"]
    applicant_rows = [
        [DIRECT_APP, "25", "Alpha Fixture Ltd", "北京市朝阳区测试路1号"],
        [MULTI_APP, "9", "Multi Fixture Ltd", "北京市朝阳区测试路1号"],
        [MULTI_APP, "42", "Multi Fixture Ltd", "北京市朝阳区测试路1号"],
        [G_ROOT, "25", "Madrid Fixture Ltd", "北京市朝阳区测试路1号"],
        [G_CHILD, "25", "Madrid Fixture Ltd", "北京市朝阳区测试路1号"],
    ]
    goods_headers = ["注册号/申请号", "国际分类", "类似群", "商品序号", "商品中文名称", "商品状态"]
    goods_rows = [
        [DIRECT_APP, "25", "2501", "1", "服装", ""],
        [DIRECT_APP, "25", "2507", "2", "鞋", "删除"],
        [MULTI_APP, "9", "0901", "1", "计算机软件", "有效"],
        [MULTI_APP, "42", "4209", "1", "软件即服务", "有效"],
        [G_ROOT, "25", "2501", "1", "服装", "有效"],
        [G_CHILD, "25", "2501", "1", "服装", "有效"],
    ]
    coowner_headers = ["注册号/申请号", "共有人中文名称", "共有人中文地址"]
    priority_headers = ["注册号/申请号", "国际分类", "优先权编号", "优先权种类", "优先权日期", "优先权商品", "优先权国家/地区"]
    madrid_headers = ["注册号/申请号", "国际注册号", "国际注册日期", "国际通知日期", "国际申请语种", "国际申请类型", "国际公告期号", "国际公告日期", "基础注册日期"]
    madrid_rows = [
        [G_ROOT, "99000001", "2019-01-01", "2019-02-01", "EN", "MADRID", "2019/01", "2019-01-15", "2018-12-01"],
        [G_CHILD, "99000001", "2019-01-01", "2019-02-01", "EN", "MADRID", "2019/01", "2019-01-15", "2018-12-01"],
    ]
    agent_headers = ["代理机构代码", "代理机构名称"]

    return {
        "注册商标基本信息.csv": _csv_bytes(basic_headers, basic_rows),
        "商标注册人信息.csv": _csv_bytes(applicant_headers, applicant_rows),
        "注册商标商品服务信息.csv": _csv_bytes(goods_headers, goods_rows),
        "注册商标共有人信息.csv": _csv_bytes(coowner_headers, [[DIRECT_APP, "Beta Coowner Ltd", "上海市浦东新区测试路2号"]]),
        "注册商标优先权信息.csv": _csv_bytes(priority_headers, [[DIRECT_APP, "25", "P-FIX-1", "PARIS", "2019-08-01", "服装", "US"]]),
        "国际注册基本信息.csv": _csv_bytes(madrid_headers, madrid_rows),
        "商标代理机构信息.csv": _csv_bytes(agent_headers, [[AGENT_CODE, "Fixture Trademark Agency"]]),
    }


def _patch_members() -> dict[str, bytes]:
    basic = _base_members()["注册商标基本信息.csv"]
    basic_lines = basic.decode("utf-8-sig").splitlines()
    direct_basic = "\n".join(basic_lines[:2]) + "\n"
    return {
        "注册商标基本信息.csv": ("\ufeff" + direct_basic).encode("utf-8"),
        "商标注册人信息.csv": _csv_bytes(
            ["注册号/申请号", "国际分类", "注册人中文名称", "注册人中文地址"],
            [[DIRECT_APP, "25", "Gamma Fixture Ltd", "北京市朝阳区更新路2号"]],
        ),
        "注册商标商品服务信息.csv": _csv_bytes(
            ["注册号/申请号", "国际分类", "类似群", "商品序号", "商品中文名称", "商品状态"],
            [
                [DIRECT_APP, "25", "2501", "1", "服装", ""],
                [DIRECT_APP, "25", "2507", "2", "鞋", "删除"],
            ],
        ),
        "商标代理机构信息.csv": _csv_bytes(
            ["代理机构代码", "代理机构名称"],
            [[AGENT_CODE, "Fixture Trademark Agency"]],
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _discovered(path: Path) -> DiscoveredPackage:
    stat = path.stat()
    return DiscoveredPackage(
        jurisdiction="CN",
        path=path,
        file_name=path.name,
        file_size=stat.st_size,
        sha256=_sha256(path),
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
    )


def main() -> None:
    started = time.perf_counter()
    package_ids: list[str] = []
    with tempfile.TemporaryDirectory(prefix="markorbit-cn-sample-") as temp_text:
        root = Path(temp_text)
        incoming = root / "incoming"
        raw_root = root / "raw"
        base_path = incoming / "2099.zip"
        patch_path = incoming / "2099_1.zip"
        _write_zip(base_path, _base_members())
        _write_zip(patch_path, _patch_members())

        base_id, base_inserted = register_package(_discovered(base_path))
        if not base_inserted:
            raise RuntimeError("bounded sample base package must register as new")
        package_ids.append(base_id)
        base_metrics = ingest_cn_package(base_id, base_path, raw_root, trigger_type="MANUAL")
        if get_package(base_id).get("status") != "SUCCESS":
            raise RuntimeError("bounded sample base package did not reach SUCCESS")

        archived_base = Path(str(get_package(base_id)["archived_path"]))
        shutil.copy2(archived_base, base_path)
        duplicate_id, duplicate_inserted = register_package(_discovered(base_path))
        if duplicate_id != base_id or duplicate_inserted:
            raise RuntimeError(
                "duplicate package registration contract failed: "
                f"base_id={base_id} duplicate_id={duplicate_id} inserted={duplicate_inserted}"
            )
        if get_package(base_id).get("status") != "SUCCESS":
            raise RuntimeError("duplicate package registration changed accepted SUCCESS status")

        patch_id, patch_inserted = register_package(_discovered(patch_path))
        if not patch_inserted:
            raise RuntimeError("bounded sample patch package must register as new")
        package_ids.append(patch_id)
        patch_metrics = ingest_cn_package(patch_id, patch_path, raw_root, trigger_type="MANUAL")
        if get_package(patch_id).get("status") != "SUCCESS":
            raise RuntimeError("bounded sample patch package did not reach SUCCESS")

        from app.db import clickhouse_client

        checks = _assert_fixture(clickhouse_client())
        checks.update(
            {
                "real_zip_parser": "PASS",
                "package_control_registration": "PASS",
                "duplicate_sha_not_reingested": "PASS",
                "base_then_monthly_patch": "PASS",
            }
        )
        report = {
            "version": FIXTURE_VERSION,
            "status": "PASS",
            "bounded": True,
            "production_corpus_touched": False,
            "full_corpus_scale_claimed": False,
            "base_package": {"file_name": "2099.zip", "metrics": base_metrics},
            "patch_package": {"file_name": "2099_1.zip", "metrics": patch_metrics},
            "checks": checks,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    for package_id in reversed(package_ids):
        try:
            _cleanup_partial_outputs(__import__("uuid").UUID(package_id))
        except Exception:
            pass


if __name__ == "__main__":
    main()
