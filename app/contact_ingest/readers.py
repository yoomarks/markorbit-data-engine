from __future__ import annotations

import io
from pathlib import Path
import shutil
import subprocess
import tempfile
from zipfile import ZipFile

from app.contact_ingest import readers_v15 as _legacy
from app.contact_ingest.directory_text_v16 import directory_contact_text_table
from app.contact_ingest.html_directory import html_directory_contact_table
from app.contact_ingest.models import TableData
from app.contact_ingest.normalization import clean_text
from app.contact_ingest.special_formats import adapt_contact_tables


SUPPORTED_EXTENSIONS = set(_legacy.SUPPORTED_EXTENSIONS) | {".josn"}
STRUCTURED_MEMBER_EXTENSIONS = SUPPORTED_EXTENSIONS - {".zip"}
_OCR_MAX_PAGES = 40

# Preserve the established V1.5 readers for formats that do not need V1.6
# compatibility handling. Module objects are re-exported intentionally so
# existing tests/operator monkeypatches continue to affect the legacy helpers.
read_xlsx_bytes = _legacy.read_xlsx_bytes
read_xls_bytes = _legacy.read_xls_bytes
read_delimited_bytes = _legacy.read_delimited_bytes
read_json_bytes = _legacy.read_json_bytes
read_txt_bytes = _legacy.read_txt_bytes
read_docx_bytes = _legacy.read_docx_bytes
read_legacy_doc_bytes = _legacy.read_legacy_doc_bytes


def read_html_bytes(data: bytes, *, source_member: str) -> list[TableData]:
    text, _encoding = _legacy._decode_text(data)
    directory = html_directory_contact_table(text, source_member=source_member)
    if directory is not None:
        return [directory]
    return _legacy.read_html_bytes(data, source_member=source_member)


def _ocr_pdf_text(data: bytes, *, source_member: str) -> str:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise ValueError(
            "PDF contains no extractable text or tables; scanned/image-only PDFs "
            "require the tesseract OCR runtime"
        )

    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - deployment dependency contract
        raise ValueError("PDF support requires the pdfplumber runtime dependency") from exc

    page_text: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages) > _OCR_MAX_PAGES:
            raise ValueError(
                f"Scanned PDF has {len(pdf.pages)} pages; OCR safety limit is "
                f"{_OCR_MAX_PAGES} pages"
            )
        for page_number, page in enumerate(pdf.pages, start=1):
            image = page.to_image(resolution=220).original
            temp_path = ""
            try:
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
                    temp_path = handle.name
                image.save(temp_path, format="PNG")
                result = subprocess.run(
                    [tesseract, temp_path, "stdout", "-l", "eng", "--psm", "11"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=180,
                )
                if result.returncode != 0:
                    error, _encoding = _legacy._decode_text(result.stderr)
                    message = clean_text(error) or "unknown OCR error"
                    raise ValueError(
                        f"OCR failed for {source_member} page {page_number}: {message}"
                    )
                text, _encoding = _legacy._decode_text(result.stdout)
                if text.strip():
                    page_text.append(text)
            finally:
                if temp_path:
                    Path(temp_path).unlink(missing_ok=True)
    if not page_text:
        raise ValueError("PDF contains no extractable text and OCR produced no usable text")
    return "\n\n".join(page_text)


def _directory_or_legacy_text(text: str, *, source_member: str) -> list[TableData]:
    directory = directory_contact_text_table(text, source_member=source_member)
    if directory is not None:
        return [directory]
    return _legacy._tables_from_text(text, source_member=source_member)


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
        adapted = adapt_contact_tables(tables)
        known = [
            table for table in adapted
            if table.sheet_name.endswith(("-aopi", "-foreign-agent"))
        ]
        if known:
            return known

    combined_text = "\n\n".join(page_text)
    if combined_text:
        directory = directory_contact_text_table(combined_text, source_member=source_member)
        if directory is not None:
            return [directory]
    if tables:
        return tables
    if combined_text:
        return _legacy._tables_from_text(combined_text, source_member=source_member)

    return _directory_or_legacy_text(
        _ocr_pdf_text(data, source_member=source_member),
        source_member=source_member,
    )


def _read_member(data: bytes, *, name: str) -> list[TableData]:
    ext = Path(name).suffix.lower()
    if ext == ".xlsx":
        return read_xlsx_bytes(data, source_member=name)
    if ext == ".xls":
        return read_xls_bytes(data, source_member=name)
    if ext in {".csv", ".tsv"}:
        return read_delimited_bytes(
            data,
            source_member=name,
            delimiter="\t" if ext == ".tsv" else None,
        )
    if ext in {".json", ".josn", ".jsonl", ".ndjson"}:
        return read_json_bytes(
            data,
            source_member=name,
            ndjson=ext in {".jsonl", ".ndjson"},
        )
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
        raise ValueError(
            f"Unsupported contact input type {ext or '<none>'}. Supported: {supported}"
        )
    data = path.read_bytes()
    if ext != ".zip":
        return adapt_contact_tables(_read_member(data, name=path.name))

    tables: list[TableData] = []
    with ZipFile(io.BytesIO(data)) as zf:
        members = [
            name for name in zf.namelist()
            if not name.endswith("/")
            and Path(name).suffix.lower() in STRUCTURED_MEMBER_EXTENSIONS
        ]
        if not members:
            raise ValueError("ZIP contains no supported structured contact files")
        for member in members:
            tables.extend(_read_member(zf.read(member), name=member))
    return adapt_contact_tables(tables)
