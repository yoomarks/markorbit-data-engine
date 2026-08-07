from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import zipfile
from typing import BinaryIO, Iterator

from app.cn.schema import FileSchema, schema_for_filename


ZIP_NAME_ENCODINGS = ("gb18030", "gbk", "cp936", "utf-8", "cp437", "latin1", "big5")
BOX_CHARS = set("╔╠▒Ω╫ó▓ß╚╦╨┼╧╣·╝╩╗∙╛╙┼╚¿▓╨■╬±┤·└φ")


def _filename_score(name: str) -> int:
    score = sum(1 for ch in name if "\u4e00" <= ch <= "\u9fff") * 20
    score -= name.count("�") * 500
    score -= sum(1 for ch in name if ch in BOX_CHARS) * 20
    if schema_for_filename(name):
        score += 10_000
    return score


def repair_zip_member_name(info: zipfile.ZipInfo) -> tuple[str, str, bool]:
    original = info.filename
    if info.flag_bits & 0x800:
        return original, "utf-8-flag", False

    try:
        raw = original.encode("cp437")
    except UnicodeEncodeError:
        return original, "original", False

    candidates: list[tuple[int, str, str]] = []
    for encoding in ZIP_NAME_ENCODINGS:
        try:
            candidate = raw.decode(encoding, errors="replace")
        except Exception:
            continue
        candidates.append((_filename_score(candidate), encoding, candidate))

    if not candidates:
        return original, "original", False

    candidates.sort(reverse=True, key=lambda item: item[0])
    score, encoding, candidate = candidates[0]
    if candidate != original and score > _filename_score(original):
        return candidate, encoding, True
    return original, "original", False


class OwnedZipStream(io.RawIOBase):
    def __init__(self, archive: zipfile.ZipFile, stream: BinaryIO):
        self._archive = archive
        self._stream = stream

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1):
        return self._stream.read(size)

    def readinto(self, buffer):
        data = self._stream.read(len(buffer))
        size = len(data)
        buffer[:size] = data
        return size

    def seekable(self) -> bool:
        return False

    def close(self) -> None:
        if not self.closed:
            try:
                self._stream.close()
            finally:
                self._archive.close()
        super().close()


@dataclass(frozen=True)
class PackageMember:
    archive_path: Path
    internal_name: str
    original_internal_name: str
    filename_encoding: str
    filename_repaired: bool
    size: int
    compressed_size: int
    schema: FileSchema | None
    top_level_member_name: str | None = None
    nested_bytes: bytes | None = None

    def open_binary(self) -> BinaryIO:
        if self.nested_bytes is not None:
            return io.BytesIO(self.nested_bytes)
        if not self.top_level_member_name:
            raise RuntimeError(f"Member has no stream source: {self.internal_name}")
        archive = zipfile.ZipFile(self.archive_path)
        stream = archive.open(self.top_level_member_name, "r")
        return io.BufferedReader(OwnedZipStream(archive, stream))

    def sample(self, size: int = 65_536) -> bytes:
        stream = self.open_binary()
        try:
            return stream.read(size)
        finally:
            stream.close()


def _iter_nested_bytes(
    archive_path: Path,
    zip_bytes: bytes,
    prefix: str,
    depth: int,
    max_depth: int,
) -> Iterator[PackageMember]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            repaired_name, encoding, repaired = repair_zip_member_name(info)
            display_name = f"{prefix}{repaired_name}"
            if repaired_name.lower().endswith(".csv"):
                data = archive.read(info)
                yield PackageMember(
                    archive_path=archive_path,
                    internal_name=display_name,
                    original_internal_name=f"{prefix}{info.filename}",
                    filename_encoding=encoding,
                    filename_repaired=repaired,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    schema=schema_for_filename(repaired_name),
                    nested_bytes=data,
                )
            elif repaired_name.lower().endswith(".zip") and depth < max_depth:
                nested_data = archive.read(info)
                yield from _iter_nested_bytes(
                    archive_path,
                    nested_data,
                    prefix=f"{display_name}!",
                    depth=depth + 1,
                    max_depth=max_depth,
                )


def iter_package_members(path: Path, max_depth: int = 2) -> Iterator[PackageMember]:
    with zipfile.ZipFile(path) as archive:
        infos = list(archive.infolist())

    for info in infos:
        if info.is_dir():
            continue
        repaired_name, encoding, repaired = repair_zip_member_name(info)
        if repaired_name.lower().endswith(".csv"):
            yield PackageMember(
                archive_path=path,
                internal_name=repaired_name,
                original_internal_name=info.filename,
                filename_encoding=encoding,
                filename_repaired=repaired,
                size=info.file_size,
                compressed_size=info.compress_size,
                schema=schema_for_filename(repaired_name),
                top_level_member_name=info.filename,
            )
        elif repaired_name.lower().endswith(".zip") and max_depth > 0:
            with zipfile.ZipFile(path) as archive:
                nested_data = archive.read(info)
            yield from _iter_nested_bytes(
                path,
                nested_data,
                prefix=f"{repaired_name}!",
                depth=1,
                max_depth=max_depth,
            )
