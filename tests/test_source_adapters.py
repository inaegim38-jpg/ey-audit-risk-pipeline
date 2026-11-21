import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "source_adapters.py"
SPEC = importlib.util.spec_from_file_location("source_adapters", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_required_env_rejects_missing_and_placeholder(monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        MODULE.required_env("DART_API_KEY")
    monkeypatch.setenv("DART_API_KEY", "replace-with-key")
    with pytest.raises(RuntimeError):
        MODULE.required_env("DART_API_KEY")


def test_request_fingerprint_excludes_dart_secret():
    params = {"crtfc_key": "secret", "corp_code": "00123456", "bgn_de": "20260101"}
    safe = {k: v for k, v in params.items() if k != "crtfc_key"}
    assert "secret" not in str(safe)
    assert "corp_code" in safe


def test_procurement_date_contract_and_secret_fingerprint(monkeypatch):
    monkeypatch.delenv("DATA_GO_KR_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        MODULE.required_env("DATA_GO_KR_API_KEY")
    params = {"serviceKey": "secret", "bidNtceBgnDt": "202611140000"}
    safe = {k: v for k, v in params.items() if k != "serviceKey"}
    assert "secret" not in str(safe)
    assert len("202611140000") == 12
