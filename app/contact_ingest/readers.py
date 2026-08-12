from __future__ import annotations

import csv
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

from app.contact_ingest.models import TableData
from app.contact_ingest.normalization import clean_text


SUPPORTED_EXTENSIONS = {
    ".xlsx",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".ndjson",
    ".txt",
    ".html",
    ".htm",
    ".pdf",
    ".docx",
    ".doc",
    ".zip",
}
STRUCTURED_MEMBER_EXTENSIONS = SUPPORTED_EXTENSIONS - {".zip"}
_XLSX_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_WORD_MAIN = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CELL_REF_RE = re.compile(r"([A-Z]+)\d+")
_KEY_VALUE_RE = re.compile(r"^\s*([^:=：]{1,100}?)\s*[:=：]\s*(.*?)\s*$")
_WHITESPACE_SPLIT_RE = re.compile(r"(?:\t+|\s{2,})")


def _column_index(ref: str) -> int:
    match = _CELL_REF_RE.fullmatch(ref.upper())
    if not match:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + (ord(char) - 64)
    return max(value - 1, 0)


def _shared_strings(zf: ZipFile) -> list[str]:
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    values: list[str] = []
    for si in root.findall(f"{{{_XLSX_MAIN}}}si"):
        parts = [node.text or "" for node in si.iter(f"{{{_XLSX_MAIN}}}t")]
        values.append("".join(parts))
    return values


def _xlsx_sheet_targets(zf: ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    relationships = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_targets = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in relationships.findall(f"{{{_PKG_REL}}}Relationship")
    }
    out: list[tuple[str, str]] = []
    for sheet in workbook.findall(f".//{{{_XLSX_MAIN}}}sheet"):
        name = sheet.attrib.get("name", "Sheet")
        rel_id = sheet.attrib.get(f"{{{_XLSX_REL}}}id", "")
        target = rel_targets.get(rel_id, "")
        if not target:
            continue
        if target.startswith("/"):
            member = target.lstrip("/")
        elif target.startswith("xl/"):
            member = target
        else:
            member = f"xl/{target.lstrip('./')}"
        out.append((name, member))
    return out


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        return clean_text("".join(node.text or "" for node in cell.iter(f"{{{_XLSX_MAIN}}}t")))
    value_node = cell.find(f"{{{_XLSX_MAIN}}}v")
    value = "" if value_node is None else value_node.text or ""
    if kind == "s" and value:
        try:
            return clean_text(shared[int(value)])
        except (ValueError, IndexError):
            return clean_text(value)
    if kind == "b":
        return "TRUE" if value == "1" else "FALSE"
    return clean_text(value)


def _parse_xlsx_zip(zf: ZipFile, *, source_member: str) -> list[TableData]:
    shared = _shared_strings(zf)
    tables: list[TableData] = []
    for sheet_name, member in _xlsx_sheet_targets(zf):
        try:
            stream = zf.open(member)
        except KeyError:
            continue
        rows: list[list[str]] = []
        with stream:
            for _, row in ET.iterparse(stream, events=("end",)):
                if row.tag != f"{{{_XLSX_MAIN}}}row":
                    continue
                values: dict[int, str] = {}
                max_idx = -1
                for cell in row.findall(f"{{{_XLSX_MAIN}}}c"):
                    ref = cell.attrib.get("r", "A1")
                    idx = _column_index(ref)
                    max_idx = max(max_idx, idx)
                    values[idx] = _cell_text(cell, shared)
                rows.append([values.get(idx, "") for idx in range(max_idx + 1)] if max_idx >= 0 else [])
                row.clear()
        tables.append(TableData(source_member=source_member, sheet_name=sheet_name, rows=rows))
    return tables


def read_xlsx_bytes(data: bytes, *, source_member: str) -> list[TableData]:
    try:
        with ZipFile(io.BytesIO(data)) as zf:
            return _parse_xlsx_zip(zf, source_member=source_member)
    except BadZipFile as exc:
        raise ValueError(f"Invalid XLSX file: {source_member}") from exc


def _decode_text(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def read_delimited_bytes(data: bytes, *, source_member: str, delimiter: str | None = None) -> list[TableData]:
    text, _encoding = _decode_text(data)
    sample = text[:65536]
    if delimiter is None:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = "\t" if source_member.lower().endswith(".tsv") else ","
    rows = [[clean_text(value) for value in row] for row in csv.reader(io.StringIO(text), delimiter=delimiter)]
    return [TableData(source_member=source_member, sheet_name="", rows=rows)]


def _dict_rows_to_table(records: list[dict[str, object]], *, source_member: str, sheet_name: str = "") -> list[TableData]:
    headers: list[str] = []
    seen: set[str] = set()
    for record in records:
        for key in record:
            key_text = clean_text(key)
            if key_text and key_text not in seen:
                seen.add(key_text)
                headers.append(key_text)
    rows = [headers]
    for record in records:
        clean_record = {clean_text(key): value for key, value in record.items()}
        rows.append([clean_text(clean_record.get(header, "")) for header in headers])
    return [TableData(source_member=source_member, sheet_name=sheet_name, rows=rows)]


def read_json_bytes(data: bytes, *, source_member: str, ndjson: bool = False) -> list[TableData]:
    text, _encoding = _decode_text(data)
    if ndjson:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        payload = json.loads(text)
        if isinstance(payload, list):
            records = payload
        elif isinstance(payload, dict):
            list_values = [value for value in payload.values() if isinstance(value, list)]
            if len(list_values) != 1:
                raise ValueError("JSON object must contain exactly one list of records")
            records = list_values[0]
        else:
            raise ValueError("JSON input must be a list of objects or object containing one record list")
    if not all(isinstance(record, dict) for record in records):
        raise ValueError("JSON contact input records must be objects")
    return _dict_rows_to_table(records, source_member=source_member)


def _json_text_table(text: str, *, source_member: str) -> list[TableData]:
    stripped = text.lstrip()
    if not stripped.startswith(("[", "{")):
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return _dict_rows_to_table(payload, source_member=source_member)
    if isinstance(payload, dict):
        if payload and all(not isinstance(value, (dict, list)) for value in payload.values()):
            return _dict_rows_to_table([payload], source_member=source_member)
        list_values = [value for value in payload.values() if isinstance(value, list)]
        if len(list_values) == 1 and all(isinstance(item, dict) for item in list_values[0]):
            return _dict_rows_to_table(list_values[0], source_member=source_member)
    return []


def _delimited_text_table(text: str, *, source_member: str) -> list[TableData]:
    nonempty = [line for line in text.splitlines() if line.strip()]
    if len(nonempty) < 2:
        return []
    sample = "\n".join(nonempty[:80])[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        return []
    rows = [
        [clean_text(value) for value in row]
        for row in csv.reader(io.StringIO("\n".join(nonempty)), delimiter=dialect.delimiter)
    ]
    widths = [len(row) for row in rows if any(row)]
    if not widths or max(widths) < 2:
        return []
    return [TableData(source_member=source_member, sheet_name="text", rows=rows)]


def _key_value_text_table(text: str, *, source_member: str) -> list[TableData]:
    records: list[dict[str, object]] = []
    current: dict[str, object] = {}
    for raw_line in text.splitlines():
        if not raw_line.strip():
            if len(current) >= 2:
                records.append(current)
            current = {}
            continue
        match = _KEY_VALUE_RE.match(raw_line)
        if not match:
            continue
        key = clean_text(match.group(1))
        value = clean_text(match.group(2))
        if not key or not value:
            continue
        if key in current and len(current) >= 2:
            records.append(current)
            current = {}
        current[key] = value
    if len(current) >= 2:
        records.append(current)
    return _dict_rows_to_table(records, source_member=source_member, sheet_name="labels") if records else []


def _whitespace_text_table(text: str, *, source_member: str) -> list[TableData]:
    rows: list[list[str]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        parts = [clean_text(part) for part in _WHITESPACE_SPLIT_RE.split(raw_line.strip())]
        if len(parts) >= 2:
            rows.append(parts)
    if len(rows) < 2:
        return []
    widths = [len(row) for row in rows[:20]]
    if max(widths) < 2:
        return []
    return [TableData(source_member=source_member, sheet_name="text", rows=rows)]


def _tables_from_text(text: str, *, source_member: str) -> list[TableData]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    for parser in (_json_text_table, _delimited_text_table, _key_value_text_table, _whitespace_text_table):
        tables = parser(text, source_member=source_member)
        if tables:
            return tables
    rows = [[clean_text(line)] for line in text.splitlines() if clean_text(line)]
    return [TableData(source_member=source_member, sheet_name="text", rows=rows)] if rows else []


def read_txt_bytes(data: bytes, *, source_member: str) -> list[TableData]:
    text, _encoding = _decode_text(data)
    return _tables_from_text(text, source_member=source_member)


class _ContactHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._ignored_depth = 0
        self.visible: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag == "table" and self._table is None:
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br":
            if self._cell is not None:
                self._cell.append(" ")
            elif self._table is None:
                self.visible.append("\n")
        elif tag in {"p", "div", "li", "section", "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6"} and self._table is None:
            self.visible.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth:
            return
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(clean_text("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(clean_text(value) for value in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None
        elif tag in {"p", "div", "li", "section", "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6"} and self._table is None:
            self.visible.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._cell is not None:
            self._cell.append(data)
        elif self._table is None:
            self.visible.append(data)


def read_html_bytes(data: bytes, *, source_member: str) -> list[TableData]:
    text, _encoding = _decode_text(data)
    parser = _ContactHTMLParser()
    parser.feed(text)
    tables = [
        TableData(source_member=source_member, sheet_name=f"table-{index}", rows=rows)
        for index, rows in enumerate(parser.tables, start=1)
    ]
    if tables:
        return tables
    return _tables_from_text("".join(parser.visible), source_member=source_member)


def _word_node_text(node: ET.Element) -> str:
    return clean_text("".join(text.text or "" for text in node.iter(f"{{{_WORD_MAIN}}}t")))


def read_docx_bytes(data: bytes, *, source_member: str) -> list[TableData]:
    try:
        with ZipFile(io.BytesIO(data)) as zf:
            root = ET.fromstring(zf.read("word/document.xml"))
    except (BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(f"Invalid DOCX file: {source_member}") from exc

    body = root.find(f"{{{_WORD_MAIN}}}body")
    if body is None:
        return []
    tables: list[TableData] = []
    paragraphs: list[str] = []
    table_index = 0
    for child in body:
        if child.tag == f"{{{_WORD_MAIN}}}tbl":
            table_index += 1
            rows: list[list[str]] = []
            for tr in child.findall(f"{{{_WORD_MAIN}}}tr"):
                row = [_word_node_text(tc) for tc in tr.findall(f"{{{_WORD_MAIN}}}tc")]
                if any(row):
                    rows.append(row)
            if rows:
                tables.append(TableData(
                    source_member=source_member,
                    sheet_name=f"table-{table_index}",
                    rows=rows,
                ))
        elif child.tag == f"{{{_WORD_MAIN}}}p":
            text = _word_node_text(child)
            if text:
                paragraphs.append(text)
    if paragraphs:
        paragraph_tables = _tables_from_text("\n".join(paragraphs), source_member=source_member)
        if tables:
            tables.extend(paragraph_tables)
        else:
            return paragraph_tables
    return tables


def read_legacy_doc_bytes(data: bytes, *, source_member: str) -> list[TableData]:
    if data.startswith(b"PK"):
        return read_docx_bytes(data, source_member=source_member)
    antiword = shutil.which("antiword")
    if not antiword:
        raise ValueError("Legacy .doc support requires the antiword runtime utility")
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as handle:
            handle.write(data)
            temp_path = handle.name
        result = subprocess.run(
            [antiword, temp_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
        if result.returncode != 0:
            error, _encoding = _decode_text(result.stderr)
            raise ValueError(f"antiword could not parse {source_member}: {clean_text(error) or 'unknown error'}")
        text, _encoding = _decode_text(result.stdout)
        return _tables_from_text(text, source_member=source_member)
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


def read_pdf_bytes(data: bytes, *, source_member: str) -> list[TableData]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - deployment dependency contract
        raise ValueError("PDF support requires the pdfplumber runtime dependency") from exc

    tables: list[TableData] = []
    page_text: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                for table_number, raw_table in enumerate(page.extract_tables() or [], start=1):
                    rows = [
                        [clean_text(cell) for cell in (row or [])]
                        for row in raw_table or []
                    ]
                    rows = [row for row in rows if any(row)]
                    if rows:
                        tables.append(TableData(
                            source_member=source_member,
                            sheet_name=f"page-{page_number}-table-{table_number}",
                            rows=rows,
                        ))
                text = page.extract_text(layout=True) or page.extract_text() or ""
                if text.strip():
                    page_text.append(text)
    except Exception as exc:
        raise ValueError(f"Invalid or unreadable PDF file: {source_member}: {exc}") from exc

    if tables:
        return tables
    if page_text:
        return _tables_from_text("\n\n".join(page_text), source_member=source_member)
    raise ValueError(
        "PDF contains no extractable text or tables; scanned/image-only PDFs require OCR"
    )


def _read_member(data: bytes, *, name: str) -> list[TableData]:
    ext = Path(name).suffix.lower()
    if ext == ".xlsx":
        return read_xlsx_bytes(data, source_member=name)
    if ext in {".csv", ".tsv"}:
        return read_delimited_bytes(data, source_member=name, delimiter="\t" if ext == ".tsv" else None)
    if ext in {".json", ".jsonl", ".ndjson"}:
        return read_json_bytes(data, source_member=name, ndjson=ext in {".jsonl", ".ndjson"})
    if ext == ".txt":
        return read_txt_bytes(data, source_member=name)
    if ext in {".html", ".htm"}:
        return read_html_bytes(data, source_member=name)
    if ext == ".pdf":
        return read_pdf_bytes(data, source_member=name)
    if ext == ".docx":
        return read_docx_bytes(data, source_member=name)
    if ext == ".doc":
        return read_legacy_doc_bytes(data, source_member=name)
    raise ValueError(f"Unsupported structured contact member: {name}")


def read_input(path: Path) -> list[TableData]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported contact input type {ext or '<none>'}. Supported: {supported}")
    data = path.read_bytes()
    if ext != ".zip":
        return _read_member(data, name=path.name)

    tables: list[TableData] = []
    with ZipFile(io.BytesIO(data)) as zf:
        members = [
            name for name in zf.namelist()
            if not name.endswith("/") and Path(name).suffix.lower() in STRUCTURED_MEMBER_EXTENSIONS
        ]
        if not members:
            raise ValueError("ZIP contains no supported structured contact files")
        for member in members:
            tables.extend(_read_member(zf.read(member), name=member))
    return tables
