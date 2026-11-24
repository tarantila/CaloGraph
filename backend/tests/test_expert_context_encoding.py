"""Prompt compaction preserves exact canonical values and record shape."""

import json

from app.expert.context_encoding import encode_context


def expand(value: object) -> object:
    if isinstance(value, dict):
        if set(value) == {"columns", "rows"}:
            return [{key: expand(item) for key, item in zip(value["columns"], row, strict=True)} for row in value["rows"]]
        return {key: expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item) for item in value]
    return value


def test_encoding_preserves_decimal_strings_missing_zero_and_nested_provenance() -> None:
    source = {
        "days": [
            {"date": "2020-01-01", "values": {"energy": "0.00", "protein": None}, "provider": "first"},
            {"date": "2026-11-14", "provider": "fallback", "values": {"energy": None, "protein": "123.4500"}},
        ],
        "single": [{"evidence_id": "profile:current", "notes": 'Untrusted "content"'}],
        "mixed": [None, 0, "0", {"different": True}],
        "empty": [],
    }
    assert expand(json.loads(encode_context(source))) == source
