"""Stand-in sheets. Each public function is the only place its sheet is touched, so it can be swapped for the real API."""

import threading
from functools import lru_cache

from openpyxl import Workbook, load_workbook

from ole5_chat.config import get_settings

_lock = threading.Lock()
RUNTIME_HEADERS = {
    "tickets": ("ticket_id", "ticket_number", "customer_email", "product", "title", "created_at", "summary"),
    "attachments": ("ticket_number", "file_name", "link", "uploaded_at"),
}


def _path(name: str):
    return get_settings().sheets_dir / f"{name}.xlsx"


def _ensure_runtime_sheet(name: str):
    """Create a missing runtime workbook without replacing existing data or configuration."""
    path = _path(name)
    if name in RUNTIME_HEADERS:
        with _lock:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                wb = Workbook()
                wb.active.append(RUNTIME_HEADERS[name])
                wb.save(path)
                wb.close()
    return path


def initialize_runtime_sheets() -> None:
    for name in RUNTIME_HEADERS:
        _ensure_runtime_sheet(name)


@lru_cache(maxsize=16)
def _rows(name: str, mtime: float) -> tuple[dict, ...]:
    wb = load_workbook(_path(name), read_only=True)
    header, *rows = wb.active.iter_rows(values_only=True)
    wb.close()
    return tuple(dict(zip(header, r)) for r in rows if any(c is not None for c in r))


def _read(name: str) -> tuple[dict, ...]:
    return _rows(name, _ensure_runtime_sheet(name).stat().st_mtime)


def _append(name: str, row: dict) -> None:
    path = _ensure_runtime_sheet(name)
    with _lock:
        wb = load_workbook(path)
        wb.active.append([row.get(c.value) for c in wb.active[1]])
        wb.save(path)
        wb.close()


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
