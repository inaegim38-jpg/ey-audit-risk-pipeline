#!/usr/bin/env python3
"""Validate external integration configuration without printing secret values.

The script deliberately fails closed: a real DART/n8n execution must not silently
fall back to synthetic data when credentials or targets are missing.
"""
import argparse
import json
import os
import sys


def require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("replace-"):
        raise ValueError(f"missing required integration setting: {name}")
    return value


def validate_targets(raw: str) -> int:
    try:
        targets = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("AEGIS_WATCH_TARGETS_JSON must be valid JSON") from exc
    if not isinstance(targets, list) or not targets:
        raise ValueError("AEGIS_WATCH_TARGETS_JSON must be a non-empty array")
    for target in targets:
        if not isinstance(target, dict) or not target.get("corp_code") or not target.get("engagement_id"):
            raise ValueError("each watch target needs corp_code and engagement_id")
    return len(targets)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--integration", choices=("dart", "all"), default="all")
    args = parser.parse_args()
    try:
        if args.integration in ("dart", "all"):
            require("DART_API_KEY")
            count = validate_targets(require("AEGIS_WATCH_TARGETS_JSON"))
            print(f"DART integration contract: PASS ({count} monitoring target(s); secret value withheld)")
        return 0
    except ValueError as exc:
        print(f"integration preflight: FAIL ({exc})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
