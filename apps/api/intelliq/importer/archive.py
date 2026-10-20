"""Safe reading of Tableau workbook files (.twb / .twbx).

Uploaded workbooks are untrusted. This module:
- never extracts to disk and never executes anything inside the package;
- bounds upload size, entry count, total and per-entry uncompressed size, and
  compression ratio (zip-bomb protection);
- rejects absolute paths, parent-directory traversal and symlink entries;
- reads only the workbook XML into memory and inventories other assets.
"""

from __future__ import annotations

import hashlib
import io
import posixpath
import stat
import zipfile
from dataclasses import dataclass, field

MAX_TWB_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 5_000
MAX_TOTAL_UNCOMPRESSED = 2 * 1024 * 1024 * 1024
MAX_RATIO = 200

ASSET_KINDS = {
    ".hyper": "extract", ".tde": "extract", ".csv": "data", ".xlsx": "data", ".xls": "data", ".json": "data",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".svg": "image",
    ".tds": "datasource", ".tms": "map", ".ttf": "font", ".otf": "font",
}  # fmt: skip


class WorkbookFileError(ValueError):
    pass


@dataclass
class Asset:
    path: str
    size: int
    kind: str


@dataclass
class WorkbookFile:
    filename: str
    sha256: str
    size: int
    packaged: bool
    twb_name: str
    twb_bytes: bytes
    assets: list[Asset] = field(default_factory=list)


def _safe_member(name: str) -> bool:
    if not name or name.startswith(("/", "\\")) or "\\" in name or "\x00" in name:
        return False
    if len(name) > 2 and name[1] == ":":  # Windows drive letter
        return False
    norm = posixpath.normpath(name)
    return not (norm.startswith("../") or norm == ".." or "/../" in f"/{norm}/")


def read_workbook(filename: str, data: bytes, *, max_bytes: int) -> WorkbookFile:
    if len(data) > max_bytes:
        raise WorkbookFileError(f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit")
    digest = hashlib.sha256(data).hexdigest()
    lower = filename.lower()

    if lower.endswith(".twb"):
        if len(data) > MAX_TWB_BYTES:
            raise WorkbookFileError("Workbook XML is too large")
        _check_xml_prefix(data)
        return WorkbookFile(filename, digest, len(data), False, filename, data)

    if not lower.endswith(".twbx"):
        raise WorkbookFileError("Only Tableau workbooks (.twb, .twbx) can be imported")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise WorkbookFileError("The .twbx file is not a valid package") from None

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ENTRIES:
            raise WorkbookFileError("Package contains too many files")
        total = 0
        twb_info = None
        assets: list[Asset] = []
        for info in infos:
            if not _safe_member(info.filename):
                raise WorkbookFileError(f"Package contains an unsafe path: {info.filename!r}")
            mode = (info.external_attr >> 16) & 0o170000
            if mode and stat.S_ISLNK(mode):
                raise WorkbookFileError("Package contains a symbolic link")
            if info.is_dir():
                continue
            total += info.file_size
            if total > MAX_TOTAL_UNCOMPRESSED:
                raise WorkbookFileError("Package expands beyond the allowed size")
            if info.compress_size and info.file_size / info.compress_size > MAX_RATIO and info.file_size > 1_000_000:
                raise WorkbookFileError("Package has a suspicious compression ratio")
            name = info.filename
            if name.lower().endswith(".twb") and "/" not in name.strip("/"):
                if twb_info is not None:
                    raise WorkbookFileError("Package contains more than one root workbook")
                twb_info = info
                continue
            ext = posixpath.splitext(name.lower())[1]
            assets.append(Asset(name, info.file_size, ASSET_KINDS.get(ext, "other")))

        if twb_info is None:
            raise WorkbookFileError("Package does not contain a workbook (.twb) at its root")
        if twb_info.file_size > MAX_TWB_BYTES:
            raise WorkbookFileError("Workbook XML is too large")
        with zf.open(twb_info) as fh:
            twb = fh.read(MAX_TWB_BYTES + 1)
        if len(twb) > MAX_TWB_BYTES:
            raise WorkbookFileError("Workbook XML is too large")
        _check_xml_prefix(twb)
        return WorkbookFile(filename, digest, len(data), True, twb_info.filename, twb, assets)


def _check_xml_prefix(data: bytes) -> None:
    head = data[:512].lstrip()
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    if not head.startswith(b"<"):
        raise WorkbookFileError("Workbook is not XML")
