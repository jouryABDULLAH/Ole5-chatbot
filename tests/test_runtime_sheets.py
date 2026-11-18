from concurrent.futures import ThreadPoolExecutor

import pytest
from openpyxl import load_workbook

from ole5_chat import sheets
from ole5_chat.config import get_settings


def test_fresh_runtime_sheets_can_be_written(tmp_path):
    get_settings().sheets_dir = tmp_path / "fresh"
    assert sheets.tickets() == ()
    sheets.add_ticket({"ticket_number": "T-NEW", "summary": "Test issue"})
    sheets.add_attachment({"ticket_number": "T-NEW", "file_name": "test.txt"})
    assert sheets.tickets()[0]["summary"] == "Test issue"
    workbook = load_workbook(get_settings().sheets_dir / "attachments.xlsx", read_only=True)
    assert list(workbook.active.values)[1][:2] == ("T-NEW", "test.txt")
    workbook.close()


def test_initialization_preserves_existing_runtime_data(sheets_dir):
    sheets.add_ticket({"ticket_number": "T-KEEP", "summary": "Do not overwrite"})
    before = (sheets_dir / "tickets.xlsx").read_bytes()
    sheets.initialize_runtime_sheets()
    assert (sheets_dir / "tickets.xlsx").read_bytes() == before
    assert sheets.tickets()[-1]["ticket_number"] == "T-KEEP"


def test_concurrent_first_reads_create_one_valid_workbook(tmp_path):
    get_settings().sheets_dir = tmp_path / "concurrent"
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(lambda _: sheets.tickets(), range(8))) == [()] * 8


def test_missing_customer_configuration_is_not_created(tmp_path):
    get_settings().sheets_dir = tmp_path / "missing-config"
    with pytest.raises(FileNotFoundError):
        sheets.customers()
    assert not (get_settings().sheets_dir / "customers.xlsx").exists()
