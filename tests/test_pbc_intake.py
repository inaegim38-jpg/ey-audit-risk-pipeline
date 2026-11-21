import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "pbc_intake.py"
SPEC = importlib.util.spec_from_file_location("pbc_intake", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_gl_csv_inspection_returns_rows_header_hash_and_zero_diff(tmp_path):
    path = tmp_path / "gl.csv"
    path.write_text(
        "doc_no,posting_date,account_code,debit,credit\n"
        "D-1,2026-03-31,1100,1000,0\n"
        "D-2,2026-03-31,4000,0,1000\n",
        encoding="utf-8",
    )
    rows, header_hash, diff = MODULE.inspect_csv(path, "GL")
    assert rows == 2
    assert len(header_hash) == 64
    assert diff == 0


def test_pbc_intake_rejects_missing_required_columns(tmp_path):
    path = tmp_path / "ar.csv"
    path.write_text("invoice_no,amount\nINV-1,100\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing required columns"):
        MODULE.inspect_csv(path, "AR")


def test_pbc_intake_rejects_empty_or_non_csv(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("doc_no,posting_date,account_code,debit,credit\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="at least one data row"):
        MODULE.inspect_csv(empty, "GL")
    with pytest.raises(RuntimeError, match="not found"):
        MODULE.ingest(tmp_path / "missing.csv", "GL", "ENG-2026-001", None, "test")


def test_pbc_intake_rejects_wrong_expected_hash(tmp_path):
    path = tmp_path / "gl.csv"
    path.write_text(
        "doc_no,posting_date,account_code,debit,credit\nD-1,2026-03-31,1100,10,10\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        MODULE.ingest(path, "GL", "ENG-2026-001", "0" * 64, "test")


def test_valid_metadata_uses_idempotent_upsert(tmp_path, monkeypatch):
    path = tmp_path / "gl.csv"
    path.write_text(
        "doc_no,posting_date,account_code,debit,credit\nD-1,2026-03-31,1100,10,10\n",
        encoding="utf-8",
    )

    class Cursor:
        def __init__(self):
            self.sql = None
            self.params = None

        def execute(self, sql, params):
            self.sql = sql
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Connection:
        def __init__(self):
            self.cursor_obj = Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return self.cursor_obj

    connection = Connection()
    monkeypatch.setattr(MODULE.psycopg2, "connect", lambda **kwargs: connection)
    MODULE.ingest(path, "GL", "ENG-2026-001", MODULE.sha256_file(path), "test")
    assert connection.cursor_obj.params[0:4] == ("ENG-2026-001", "gl.csv", MODULE.sha256_file(path), 1)
    assert connection.cursor_obj.params[5] == "PASSED"
    assert "ON CONFLICT (engagement_id, sha256_hash) DO UPDATE" in connection.cursor_obj.sql
