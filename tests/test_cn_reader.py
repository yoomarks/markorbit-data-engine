from pathlib import Path
import zipfile

from app.cn.reader import iter_member_rows
from app.cn.zipio import iter_package_members


HEADER = (
    "注册号/申请号,国际分类,申请日期,商标名称,商标类型,代理机构代码,"
    "初审公告期号,初审公告日期,注册公告期号,注册公告日期,"
    "专用期开始日期,专用期结束日期,专用有效期,商标设计说明,"
    "商标颜色说明,放弃专用权说明,是否立体商标,是否共有申请,"
    "商标形态,地理标志信息,颜色标志,是否驰名商标"
)


def make_zip(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("注册商标基本信息.csv", text.encode("utf-8"))
    return path


def test_multiline_and_unbalanced_quote_do_not_absorb_next_record(tmp_path: Path):
    row1 = (
        '12345678,9,2023-01-01,TEST,普通,100,,,,,,,,'
        '"第一行说明\n第二行说明,,,,否,否,文字,,,否'
    )
    row2 = "12345679,35,2023-01-02,NEXT,普通,101,,,,,,,,,,,,否,否,文字,,,否"
    path = make_zip(tmp_path, "sample.zip", HEADER + "\n" + row1 + "\n" + row2 + "\n")

    member = next(iter_package_members(path))
    profile, rows = iter_member_rows(member)
    parsed = list(rows)

    assert len(parsed) == 2
    assert parsed[0].record["application_number"] == "12345678"
    assert parsed[1].record["application_number"] == "12345679"
    assert profile.continuation_rows == 1
    assert profile.failed_rows == 0


def test_header_alias_announcement_date_is_role_scoped(tmp_path: Path):
    header = HEADER.replace("初审公告日期", "初审公告日期>日期")
    values = [""] * 22
    values[0:8] = ["12345678", "9", "2023-01-01", "TEST", "普通", "100", "1", "2023-02-01"]
    values[16:22] = ["否", "否", "文字", "", "", "否"]
    row = ",".join(values)
    path = make_zip(tmp_path, "sample.zip", header + "\n" + row + "\n")

    member = next(iter_package_members(path))
    profile, rows = iter_member_rows(member)
    parsed = list(rows)

    assert profile.header_canonical[7] == "prelim_pub_date"
    assert parsed[0].record["prelim_pub_date"] == "2023-02-01"


def test_quoted_comma_is_preserved_in_design_description(tmp_path: Path):
    row = (
        '12345678,9,2023-01-01,TEST,普通,100,,,,,,,,'
        '"含有,逗号的说明",,,,否,否,文字,,,否'
    )
    path = make_zip(tmp_path, "sample.zip", HEADER + "\n" + row + "\n")

    member = next(iter_package_members(path))
    _, rows = iter_member_rows(member)
    parsed = list(rows)

    assert len(parsed) == 1
    assert "含有,逗号的说明" in parsed[0].record["design_description"]
