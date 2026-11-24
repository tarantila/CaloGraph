"""Lossless prompt compression of repeated JSON records, without recalculation."""

import json


def compact_data(value: object) -> object:
    if isinstance(value, dict):
        return {key: compact_data(item) for key, item in value.items()}
    if isinstance(value, list):
        if len(value) > 1 and all(isinstance(item, dict) for item in value):
            columns = list(value[0])
            if all(set(item) == set(columns) for item in value):
                return {"columns": columns, "rows": [[compact_data(item[column]) for column in columns] for item in value]}
        return [compact_data(item) for item in value]
    return value


def encode_context(value: object) -> str:
    return json.dumps(compact_data(value), ensure_ascii=False, separators=(",", ":"))
