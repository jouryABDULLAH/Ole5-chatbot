"""Stand-in sheets. Each public function is the only place its sheet is touched, so it can be swapped for the real API."""

import threading
from functools import lru_cache

from openpyxl import load_workbook

from ole5_chat.config import get_settings

_lock = threading.Lock()


def _path(name: str):
    return get_settings().sheets_dir / f"{name}.xlsx"


@lru_cache(maxsize=16)
def _rows(name: str, mtime: float) -> tuple[dict, ...]:
    wb = load_workbook(_path(name), read_only=True)
    header, *rows = wb.active.iter_rows(values_only=True)
    wb.close()
    return tuple(dict(zip(header, r)) for r in rows if any(c is not None for c in r))


def _read(name: str) -> tuple[dict, ...]:
    return _rows(name, _path(name).stat().st_mtime)


def _append(name: str, row: dict) -> None:
    with _lock:
        wb = load_workbook(_path(name))
        wb.active.append([row.get(c.value) for c in wb.active[1]])
        wb.save(_path(name))


def customers() -> tuple[dict, ...]:
    return _read("customers")


def values() -> tuple[dict, ...]:
    return _read("values")


def tickets() -> tuple[dict, ...]:
    return _read("tickets")


def add_ticket(row: dict) -> None:
    _append("tickets", row)


def add_attachment(row: dict) -> None:
    _append("attachments", row)
