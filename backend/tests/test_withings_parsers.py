from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.withings.constants import WITHINGS_MAX_PAGES, WITHINGS_MAX_RECORDS
from app.withings.errors import WithingsClientError, WithingsInvalidResponseError
from app.withings.parsers import (
    parse_activity_pages,
    parse_activity_response,
    parse_measure_pages,
    parse_measure_response,
)

_OMITTED_TIMEZONE = object()


def test_measure_parser_applies_exact_decimal_exponent_and_preserves_provider_timezone() -> None:
    parsed = parse_measure_response(
        {
            "status": 0,
            "body": {
                "timezone": "Europe/Berlin",
                "measuregrps": [
                    {
                        "grpid": 987,
                        "date": 1704067200,
                        "measures": [
                            {"type": 1, "value": 81234, "unit": -3},
                            {"type": 6, "value": 2345, "unit": -2},
                            {"type": 999, "value": 123, "unit": 0},
                        ],
                    }
                ],
                "more": 0,
                "offset": 0,
            },
        }
    )

    assert len(parsed.groups) == 1
    group = parsed.groups[0]
    assert group.group_id == 987
    assert group.timezone == "Europe/Berlin"
    assert group.measured_at.tzinfo is not None
    assert group.measured_at.date() == date(2024, 1, 1)
    assert [(item.measure_type, item.value) for item in group.measures] == [
        (1, Decimal("81.234")),
        (6, Decimal("23.45")),
    ]


def test_measure_parser_preserves_decimal_value_of_json_float() -> None:
    parsed = parse_measure_response(
        {
            "status": 0,
            "body": {
                "measuregrps": [
                    {
                        "grpid": 1,
                        "date": 1700000000,
                        "measures": [{"type": 1, "value": 0.1, "unit": 0}],
                    }
                ]
            },
        }
    )

    assert parsed.groups[0].measures[0].value == Decimal("0.1")


def test_measure_parser_rejects_bad_shape_without_raw_payload() -> None:
    for payload in (
        {"status": 0, "body": {"measuregrps": "not-a-list", "error": "secret-provider-detail"}},
        {"status": 0, "body": {"measuregrps": [{"grpid": 1, "date": "bad", "measures": []}]}},
        {
            "status": 0,
            "body": {
                "measuregrps": [{"grpid": 1, "date": "bad", "timezone": "UTC", "measures": []}]
            },
        },
    ):
        with pytest.raises(WithingsInvalidResponseError) as caught:
            parse_measure_response(payload)
        assert "secret-provider-detail" not in str(caught.value)
        assert caught.value.code == "invalid_response"


def test_measure_parser_rejects_malformed_json_without_raw_body() -> None:
    with pytest.raises(WithingsInvalidResponseError) as caught:
        parse_measure_response(b'{"status":0,"private":"provider payload",')

    assert caught.value.code == "invalid_response"
    assert caught.value.diagnostic is not None
    assert caught.value.diagnostic.structural_reason == "malformed_json"
    assert "provider payload" not in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", Decimal("NaN")],
)
def test_measure_parser_rejects_nonfinite_values(value: object) -> None:
    response = {
        "status": 0,
        "body": {
            "measuregrps": [
                {"grpid": 1, "date": 1700000000, "measures": [{"type": 1, "value": value, "unit": 0}]}
            ]
        },
    }

    with pytest.raises(WithingsInvalidResponseError) as caught:
        parse_measure_response(response)

    assert caught.value.diagnostic is not None
    assert caught.value.diagnostic.field_path == "body.measuregrps[0].measures[0].value"


def test_activity_parser_uses_documented_string_dates_as_civil_days() -> None:
    parsed = parse_activity_response(
        {
            "status": 0,
            "body": {
                "activities": [
                    {
                        "date": "2020-06-24",
                        "timezone": "Pacific/Kiritimati",
                        "calories": 0,
                    },
                    {
                        "date": "2024-02-29",
                        "timezone": None,
                        "calories": 321.50,
                        "totalcalories": 9999,
                    },
                    {"date": "2024-03-01", "calories": 12},
                ],
                "more": 0,
                "offset": 0,
            },
        }
    )

    assert [(item.civil_date, item.calories) for item in parsed.activities] == [
        (date(2020, 6, 24), Decimal("0")),
        (date(2024, 2, 29), Decimal("321.5")),
        (date(2024, 3, 1), Decimal("12")),
    ]


@pytest.mark.parametrize(
    ("activity", "reason", "presence", "observed_json_type"),
    [
        (
            {"dateymd": "2020-06-24", "calories": 1},
            "activity_date_missing",
            "missing",
            None,
        ),
        (
            {"date": 1704067200, "calories": 1},
            "activity_date_invalid_type",
            "present",
            "number",
        ),
        (
            {"date": None, "calories": 1},
            "activity_date_invalid_type",
            "present",
            "null",
        ),
        (
            {"date": "2020-02-30", "calories": 1},
            "activity_date_invalid",
            "present",
            "string",
        ),
        (
            {"date": "2020-6-24", "calories": 1},
            "activity_date_invalid",
            "present",
            "string",
        ),
    ],
)
def test_activity_parser_reports_precise_date_failures(
    activity: dict[str, object],
    reason: str,
    presence: str,
    observed_json_type: str | None,
) -> None:
    with pytest.raises(WithingsInvalidResponseError) as caught:
        parse_activity_response({"status": 0, "body": {"activities": [activity]}})

    error = caught.value
    assert error.code == "invalid_response"
    assert error.diagnostic is not None
    assert error.diagnostic.structural_reason == reason
    assert error.diagnostic.presence == presence
    assert error.diagnostic.observed_json_type == observed_json_type
    assert error.diagnostic.field_path == "body.activities[0].date"


@pytest.mark.parametrize("timezone", [None, "", "Pacific/Kiritimati"])
def test_activity_parser_accepts_optional_string_timezone_metadata(
    timezone: str | None,
) -> None:
    parsed = parse_activity_response(
        {
            "status": 0,
            "body": {
                "activities": [
                    {"date": "2020-06-24", "timezone": timezone, "calories": 1}
                ]
            },
        }
    )

    assert parsed.activities[0].civil_date == date(2020, 6, 24)


@pytest.mark.parametrize("timezone", [42, True, []])
def test_activity_parser_rejects_non_string_timezone_metadata(timezone: object) -> None:
    with pytest.raises(WithingsInvalidResponseError) as caught:
        parse_activity_response(
            {
                "status": 0,
                "body": {
                    "activities": [
                        {"date": "2020-06-24", "timezone": timezone, "calories": 1}
                    ]
                },
            }
        )

    assert caught.value.code == "invalid_response"
    assert caught.value.diagnostic is not None
    assert caught.value.diagnostic.structural_reason == "activity_timezone_invalid_type"
    assert caught.value.diagnostic.field_path == "body.activities[0].timezone"


def test_activity_parser_never_falls_back_to_totalcalories_or_steps() -> None:
    with pytest.raises(WithingsInvalidResponseError) as caught:
        parse_activity_response(
            {
                "status": 0,
                "body": {
                    "activities": [
                        {
                            "date": "2020-06-24",
                            "steps": 1234,
                            "totalcalories": 123,
                        }
                    ],
                    "more": 0,
                    "offset": 0,
                },
            }
        )

    assert caught.value.code == "invalid_response"
    assert "activity_calories_missing" in str(caught.value)


def test_activity_parser_rejects_negative_official_calories() -> None:
    response = {
        "status": 0,
        "body": {
            "activities": [{"date": "2026-09-25", "calories": -1}],
            "more": 0,
            "offset": 0,
        },
    }

    with pytest.raises(WithingsInvalidResponseError):
        parse_activity_response(response)


def test_activity_parser_preserves_decimal_value_of_json_float() -> None:
    parsed = parse_activity_response(
        {
            "status": 0,
            "body": {
                "activities": [{"date": "2026-09-25", "calories": 0.1}],
                "more": 0,
                "offset": 0,
            },
        }
    )

    assert parsed.activities[0].calories == Decimal("0.1")


def test_activity_parser_rejects_records_beyond_provider_page_size() -> None:
    activity = {"date": "2026-09-25", "calories": 200}
    response = {
        "status": 0,
        "body": {"activities": [activity] * 100, "more": 0, "offset": 0},
    }
    assert len(parse_activity_response(response).activities) == 100

    response["body"]["activities"] = [activity] * 101
    with pytest.raises(WithingsInvalidResponseError) as caught:
        parse_activity_response(response)

    assert caught.value.code == "invalid_response"
    assert caught.value.diagnostic is not None
    assert caught.value.diagnostic.field_path == "body.activities"

def test_activity_pages_accept_same_response_offset_on_terminal_page() -> None:
    pages = parse_activity_pages(
        [
            {"status": 0, "body": {"activities": [], "more": True, "offset": 175}},
            {"status": 0, "body": {"activities": [], "more": False, "offset": 175}},
        ]
    )

    assert [page.offset for page in pages] == [175, 175]
    assert pages[0].next_offset == 175
    assert pages[1].next_offset is None


def test_activity_pages_reject_response_after_terminal_page() -> None:
    responses = [
        {"status": 0, "body": {"activities": [], "more": False, "offset": 0}},
        {"status": 0, "body": {"activities": [], "more": False, "offset": 100}},
    ]

    with pytest.raises(WithingsInvalidResponseError):
        parse_activity_pages(responses)


def test_activity_pages_reject_nonprogressing_cursor_when_more_is_true() -> None:
    responses = [{"status": 0, "body": {"activities": [], "more": True, "offset": 0}}]

    with pytest.raises(WithingsInvalidResponseError):
        parse_activity_pages(responses)


def test_activity_pages_reject_repeated_cursor_when_more_is_true() -> None:
    responses = [
        {"status": 0, "body": {"activities": [], "more": True, "offset": 175}},
        {"status": 0, "body": {"activities": [], "more": True, "offset": 175}},
    ]

    with pytest.raises(WithingsInvalidResponseError):
        parse_activity_pages(responses)


def test_activity_parser_uses_provider_offset_as_next_cursor() -> None:
    page = parse_activity_response(
        {
            "status": 0,
            "body": {"activities": [], "more": True, "offset": 175},
        }
    )

    assert page.offset == 175
    assert page.more is True
    assert page.next_offset == 175


def test_activity_pagination_rejects_duplicate_or_non_progressing_offsets_and_bounds_pages() -> (
    None
):
    page = {"status": 0, "body": {"activities": [], "more": 1, "offset": 0}}
    with pytest.raises(WithingsInvalidResponseError):
        parse_measure_pages([page, page])

    pages = [
        {"status": 0, "body": {"measuregrps": [], "more": 1, "offset": (i + 1) * 100}}
        for i in range(WITHINGS_MAX_PAGES + 1)
    ]
    with pytest.raises(WithingsInvalidResponseError):
        parse_measure_pages(pages)

    assert WITHINGS_MAX_RECORDS >= WITHINGS_MAX_PAGES


def test_measure_parser_accepts_minimal_response_without_provider_timezone() -> None:
    parsed = parse_measure_response(
        {
            "status": 0,
            "body": {
                "measuregrps": [
                    {
                        "grpid": 987,
                        "date": 1704065400,
                        "measures": [{"type": 1, "value": 81234, "unit": -3}],
                    }
                ]
            },
        }
    )

    group = parsed.groups[0]
    assert group.measured_at == datetime(2023, 12, 31, 23, 30, tzinfo=UTC)
    assert group.timezone is None
    assert group.local_date is None
    assert group.measures[0].value == Decimal("81.234")


def test_group_local_date_treats_z_timezone_as_utc() -> None:
    parsed = parse_measure_response(
        {
            "status": 0,
            "body": {
                "measuregrps": [
                    {
                        "grpid": 1,
                        "date": 1704067200,
                        "timezone": "Z",
                        "measures": [],
                    }
                ]
            },
        }
    )

    assert parsed.groups[0].local_date == date(2024, 1, 1)


def test_group_timezone_extension_preserves_provider_local_day() -> None:
    parsed = parse_measure_response(
        {
            "status": 0,
            "body": {
                "measuregrps": [
                    {
                        "grpid": 44,
                        "date": 1704069000,
                        "timezone": "America/Los_Angeles",
                    }
                ]
            },
        }
    )

    group = parsed.groups[0]
    assert group.timezone == "America/Los_Angeles"
    assert group.measured_at.date() == date(2023, 12, 31)
    assert group.local_date == date(2023, 12, 31)


@pytest.mark.parametrize(
    ("body_timezone", "group_timezone", "expected_timezone"),
    [
        ("Europe/Berlin", "America/Los_Angeles", "America/Los_Angeles"),
        ("Europe/Berlin", None, "Europe/Berlin"),
        ("Europe/Berlin", _OMITTED_TIMEZONE, "Europe/Berlin"),
        (None, None, None),
        (None, _OMITTED_TIMEZONE, None),
        (_OMITTED_TIMEZONE, None, None),
        (_OMITTED_TIMEZONE, _OMITTED_TIMEZONE, None),
        (None, "America/Los_Angeles", "America/Los_Angeles"),
        (_OMITTED_TIMEZONE, "America/Los_Angeles", "America/Los_Angeles"),
    ],
)
def test_measure_parser_falls_back_across_nullable_provider_timezones(
    body_timezone: object,
    group_timezone: object,
    expected_timezone: str | None,
) -> None:
    group: dict[str, object] = {
        "grpid": 44,
        "date": 1704065400,
        "measures": [],
    }
    body: dict[str, object] = {"measuregrps": [group]}
    if body_timezone is not _OMITTED_TIMEZONE:
        body["timezone"] = body_timezone
    if group_timezone is not _OMITTED_TIMEZONE:
        group["timezone"] = group_timezone

    parsed = parse_measure_response({"status": 0, "body": body})
    result = parsed.groups[0]

    assert result.timezone == expected_timezone
    assert result.measured_at.tzinfo is not None
    assert result.measured_at.astimezone(UTC) == datetime(2023, 12, 31, 23, 30, tzinfo=UTC)
    if expected_timezone is None:
        assert result.local_date is None


@pytest.mark.parametrize(
    ("field", "invalid_timezone", "observed_json_type"),
    [
        ("group", 1, "number"),
        ("group", {}, "object"),
        ("group", [], "array"),
        ("group", False, "boolean"),
        ("group", "", "string"),
        ("body", 1, "number"),
        ("body", {}, "object"),
        ("body", [], "array"),
        ("body", False, "boolean"),
        ("body", "", "string"),
    ],
)
def test_measure_parser_rejects_invalid_timezone_values_with_safe_diagnostics(
    field: str,
    invalid_timezone: object,
    observed_json_type: str,
) -> None:
    group: dict[str, object] = {
        "grpid": 44,
        "date": 1704065400,
        "measures": [],
    }
    body: dict[str, object] = {"timezone": "Europe/Berlin", "measuregrps": [group]}
    if field == "group":
        group["timezone"] = invalid_timezone
        field_path = "body.measuregrps[0].timezone"
    else:
        body["timezone"] = invalid_timezone
        group["timezone"] = "America/Los_Angeles"
        field_path = "body.timezone"

    with pytest.raises(WithingsInvalidResponseError) as caught:
        parse_measure_response({"status": 0, "body": body})

    diagnostic = caught.value.diagnostic
    assert diagnostic is not None
    assert diagnostic.parser_stage == "measure_response"
    assert diagnostic.field_path == field_path
    assert diagnostic.validation_rule == "measure_timezone_invalid"
    assert diagnostic.structural_reason == "measure_timezone_invalid"
    assert diagnostic.presence == "present"
    assert diagnostic.observed_json_type == observed_json_type
    assert diagnostic.expected_json_type == "timezone string"
    assert diagnostic.group_index == (0 if field == "group" else None)
    assert diagnostic.measurement_index is None


def test_measure_parser_preserves_utc_instant_across_dst_transition() -> None:
    parsed = parse_measure_response(
        {
            "status": 0,
            "body": {
                "measuregrps": [
                    {
                        "grpid": 45,
                        "date": 1710055800,
                        "timezone": "America/New_York",
                        "measures": [],
                    }
                ]
            },
        }
    )

    group = parsed.groups[0]
    assert group.measured_at.astimezone(UTC) == datetime(2024, 3, 10, 7, 30, tzinfo=UTC)
    assert group.measured_at.hour == 3
    assert group.measured_at.utcoffset() is not None
    assert group.local_date == date(2024, 3, 10)


@pytest.mark.parametrize("device_id", ["device-serial", 12345])
def test_measure_parser_tolerates_optional_metadata_and_unknown_extensions(
    device_id: object,
) -> None:
    parsed = parse_measure_response(
        {
            "status": 0,
            "body": {
                "timezone": "Europe/Berlin",
                "updatetime": "2024-01-01T12:00:00+01:00",
                "measuregrps": [
                    {
                        "grpid": 987,
                        "attrib": 0,
                        "date": 1704067200,
                        "created": 1704067200,
                        "modified": 1704067201,
                        "category": 1,
                        "deviceid": device_id,
                        "hash_deviceid": "opaque-device",
                        "model": "Scale",
                        "model_id": 1,
                        "comment": None,
                        "future_group_field": {"ignored": True},
                        "measures": [
                            {
                                "type": 1,
                                "value": 81234,
                                "unit": -3,
                                "algo": 1,
                                "fm": 0,
                                "position": 0,
                                "future_measure_field": "ignored",
                            }
                        ],
                    }
                ],
            },
        }
    )

    assert parsed.groups[0].measures[0].value == Decimal("81.234")


def test_empty_measure_groups_are_valid_without_optional_timezone() -> None:
    parsed = parse_measure_response({"status": 0, "body": {"measuregrps": []}})

    assert parsed.groups == ()
    assert parsed.more is False
    assert parsed.next_offset is None


def test_nonzero_measure_status_is_a_provider_error_with_numeric_status_only() -> None:
    with pytest.raises(WithingsClientError) as caught:
        parse_measure_response({"status": 100, "body": {"error": "private provider message"}})
    assert caught.value.code == "provider_error"

    assert caught.value.provider_status_code == 100
    assert "private provider message" not in str(caught.value)


@pytest.mark.parametrize(
    ("response", "field_path", "presence", "observed_json_type", "expected_json_type"),
    [
        ({"status": 0}, "body", "missing", None, "object"),
        (
            {"status": 0, "body": {"measuregrps": "not-a-list"}},
            "body.measuregrps",
            "present",
            "string",
            "array",
        ),
        (
            {
                "status": 0,
                "body": {
                    "timezone": "UTC",
                    "measuregrps": [
                        {
                            "grpid": 1,
                            "date": 1704067200,
                            "measures": [{"type": 1, "value": 825}],
                        }
                    ],
                },
            },
            "body.measuregrps[0].measures[0].unit",
            "missing",
            None,
            "integer",
        ),
    ],
)
def test_measure_parser_errors_include_bounded_field_diagnostics(
    response: object,
    field_path: str,
    presence: str,
    observed_json_type: str | None,
    expected_json_type: str,
) -> None:
    with pytest.raises(WithingsInvalidResponseError) as caught:
        parse_measure_response(response)

    diagnostic = caught.value.diagnostic
    assert diagnostic.parser_stage == "measure_response"
    assert diagnostic.field_path == field_path
    assert diagnostic.presence == presence
    assert diagnostic.observed_json_type == observed_json_type
    assert diagnostic.expected_json_type == expected_json_type
    assert "private" not in str(caught.value)
    assert diagnostic.validation_rule
    assert diagnostic.structural_reason == diagnostic.validation_rule
    if field_path.endswith(".unit"):
        assert diagnostic.group_index == 0
        assert diagnostic.measurement_index == 0


def test_measure_pagination_uses_provider_next_offset_and_requested_offset() -> None:
    first = {
        "status": 0,
        "body": {"measuregrps": [], "more": 1, "offset": 100},
    }
    second = {"status": 0, "body": {"measuregrps": [], "more": 0}}

    pages = parse_measure_pages([first, second])
    assert [page.offset for page in pages] == [0, 100]
    assert pages[0].next_offset == 100
    assert pages[1].next_offset is None


def test_measure_next_offset_can_exceed_record_limit() -> None:
    next_offset = WITHINGS_MAX_RECORDS + 1
    page = parse_measure_response(
        {
            "status": 0,
            "body": {"measuregrps": [], "more": 1, "offset": next_offset},
        }
    )

    assert page.next_offset == next_offset


@pytest.mark.parametrize(
    "body",
    [
        {"measuregrps": [], "more": 1, "offset": 0},
        {"measuregrps": [], "more": 1},
        {"measuregrps": [], "more": 1, "offset": -1},
        {"measuregrps": [], "more": True, "offset": 100},
        {"measuregrps": [], "more": "1", "offset": 100},
        {"measuregrps": [], "more": 1, "offset": 1 << 63},
    ],
)
def test_measure_pagination_rejects_missing_repeated_or_invalid_next_offset(
    body: dict[str, object],
) -> None:
    with pytest.raises(WithingsInvalidResponseError):
        parse_measure_response({"status": 0, "body": body}, requested_offset=0)


def test_measure_pages_reject_responses_after_final_page() -> None:
    with pytest.raises(WithingsInvalidResponseError):
        parse_measure_pages(
            [
                {"status": 0, "body": {"measuregrps": [], "more": 0}},
                {"status": 0, "body": {"measuregrps": [], "more": 0}},
            ]
        )
