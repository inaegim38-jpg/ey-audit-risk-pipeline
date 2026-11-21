import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "integration_preflight.py"
SPEC = importlib.util.spec_from_file_location("integration_preflight", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
validate_targets = MODULE.validate_targets


def test_watch_targets_require_corp_code_and_engagement():
    assert validate_targets('[{"corp_code":"00123456","engagement_id":"ENG-2026-001"}]') == 1


def test_watch_targets_reject_empty_or_malformed_payload():
    with pytest.raises(ValueError):
        validate_targets('[]')
    with pytest.raises(ValueError):
        validate_targets('[{"corp_code":"00123456"}]')
    with pytest.raises(ValueError):
        validate_targets('not-json')
