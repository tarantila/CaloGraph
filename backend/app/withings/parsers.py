from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.withings.constants import (
    WITHINGS_API_STATUS_OK,
    WITHINGS_MAX_PAGE_SIZE,
    WITHINGS_MAX_PAGES,
    WITHINGS_MAX_RECORDS,
    WITHINGS_MEASURE_TYPES,
)
from app.withings.errors import (
    WithingsInvalidResponseError,
    WithingsParserDiagnostic,
    WithingsProviderError,
)


@dataclass(frozen=True, slots=True)
class WithingsMeasure:
    measure_type: int
    metric_type: str
    unit: str
    value: Decimal
    original_value: Decimal
    exponent: int

    @property
    def type(self) -> int:
        return self.measure_type


@dataclass(frozen=True, slots=True)
class WithingsMeasureGroup:
    group_id: int
    measured_at: datetime
    timezone: str | None
    measures: tuple[WithingsMeasure, ...]

    @property
    def grpid(self) -> int:
        return self.group_id

    @property
    def local_date(self) -> date | None:
        if self.timezone is None:
            return None
        zone = UTC if self.timezone in {"UTC", "GMT", "Z"} else ZoneInfo(self.timezone)
        return self.measured_at.astimezone(zone).date()


@dataclass(frozen=True, slots=True)
class MeasurePage:
    groups: tuple[WithingsMeasureGroup, ...]
    offset: int
    more: bool
    next_offset: int | None

    @property
    def measuregrps(self) -> tuple[WithingsMeasureGroup, ...]:
        return self.groups


@dataclass(frozen=True, slots=True)
class WithingsActivity:
    civil_date: date
    calories: Decimal

    @property
    def date(self) -> date:
        return self.civil_date


@dataclass(frozen=True, slots=True)
class ActivityPage:
    activities: tuple[WithingsActivity, ...]
    offset: int
    more: bool
    next_offset: int | None


_MISSING = object()
_MAX_MEASURE_OFFSET = (1 << 63) - 1


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int | float | Decimal):
        return "number"
    return "unknown"


def _field_error(
    reason: str,
    *,
    parser_stage: str,
    field_path: str,
    expected_json_type: str | None,
    observed: object = _MISSING,
    group_index: int | None = None,
    measurement_index: int | None = None,
) -> WithingsInvalidResponseError:
    presence: Literal["present", "missing"] = "missing" if observed is _MISSING else "present"
    diagnostic = WithingsParserDiagnostic(
        parser_stage=parser_stage,
        field_path=field_path,
        validation_rule=reason,
        structural_reason=reason,
        presence=presence,
        observed_json_type=None if presence == "missing" else _json_type(observed),
        expected_json_type=expected_json_type,
        group_index=group_index,
        measurement_index=measurement_index,
    )
    return WithingsInvalidResponseError(diagnostic=diagnostic)


def _invalid(reason: str) -> WithingsInvalidResponseError:
    # Activity-parser reasons are fixed categories, never provider-controlled data.
    return WithingsInvalidResponseError(f"Withings invalid response: {reason}")


def _json_payload(response: object, *, parser_stage: str) -> object:
    if isinstance(response, Mapping):
        return response
    if isinstance(response, (bytes, bytearray)):
        try:
            import json

            return json.loads(response)
        except TypeError, ValueError:
            raise _field_error(
                "malformed_json",
                parser_stage=parser_stage,
                field_path="response",
                expected_json_type="JSON object",
                observed=response,
            ) from None
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            return json_method()
        except Exception:
            raise _field_error(
                "malformed_json",
                parser_stage=parser_stage,
                field_path="response",
                expected_json_type="JSON object",
                observed=response,
            ) from None
    raise _field_error(
        "unsupported_response",
        parser_stage=parser_stage,
        field_path="response",
        expected_json_type="JSON object",
        observed=response,
    )


def _body(response: object, *, parser_stage: str) -> Mapping[str, object]:
    payload = _json_payload(response, parser_stage=parser_stage)
    if not isinstance(payload, Mapping):
        raise _field_error(
            "top_level_not_object",
            parser_stage=parser_stage,
            field_path="response",
            expected_json_type="object",
            observed=payload,
        )
    status = payload.get("status", _MISSING)
    if isinstance(status, bool) or not isinstance(status, int):
        raise _field_error(
            "status_missing_or_invalid",
            parser_stage=parser_stage,
            field_path="status",
            expected_json_type="integer",
            observed=status,
        )
    if status != WITHINGS_API_STATUS_OK:
        raise WithingsProviderError(status)
    body = payload.get("body", _MISSING)
    if not isinstance(body, Mapping):
        raise _field_error(
            "body_missing_or_invalid",
            parser_stage=parser_stage,
            field_path="body",
            expected_json_type="object",
            observed=body,
        )
    return body


def _int(
    value: object,
    reason: str,
    *,
    minimum: int = 0,
    parser_stage: str = "parser",
    field_path: str = "response",
    group_index: int | None = None,
    measurement_index: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _field_error(
            reason,
            parser_stage=parser_stage,
            field_path=field_path,
            expected_json_type="integer",
            observed=value,
            group_index=group_index,
            measurement_index=measurement_index,
        )
    return value


def _more(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise _invalid("pagination_more_invalid")


def _decimal(
    value: object,
    reason: str,
    *,
    parser_stage: str = "parser",
    field_path: str = "response",
    group_index: int | None = None,
    measurement_index: int | None = None,
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise _field_error(
            reason,
            parser_stage=parser_stage,
            field_path=field_path,
            expected_json_type="integer, number, or numeric string",
            observed=value,
            group_index=group_index,
            measurement_index=measurement_index,
        )
    try:
        parsed = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation, ValueError, TypeError:
        raise _field_error(
            reason,
            parser_stage=parser_stage,
            field_path=field_path,
            expected_json_type="integer, number, or numeric string",
            observed=value,
            group_index=group_index,
            measurement_index=measurement_index,
        ) from None
    if not parsed.is_finite():
        raise _field_error(
            reason,
            parser_stage=parser_stage,
            field_path=field_path,
            expected_json_type="finite numeric value",
            observed=value,
            group_index=group_index,
            measurement_index=measurement_index,
        )
    return parsed


def _timestamp(value: object, timezone_name: str | None, *, group_index: int) -> datetime:
    timestamp = _int(
        value,
        "measure_timestamp_invalid",
        parser_stage="measure_response",
        field_path=f"body.measuregrps[{group_index}].date",
        group_index=group_index,
    )
    zone: tzinfo = UTC
    if timezone_name is not None:
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError, ValueError:
            if timezone_name in {"UTC", "GMT", "Z"}:
                zone = UTC
            else:
                raise _field_error(
                    "measure_timezone_invalid",
                    parser_stage="measure_response",
                    field_path="body.timezone",
                    expected_json_type="valid IANA timezone string",
                    observed=timezone_name,
                    group_index=group_index,
                ) from None
    try:
        return datetime.fromtimestamp(timestamp, tz=zone)
    except OverflowError, OSError, ValueError:
        raise _field_error(
            "measure_timestamp_invalid",
            parser_stage="measure_response",
            field_path=f"body.measuregrps[{group_index}].date",
            expected_json_type="valid Unix timestamp",
            observed=value,
            group_index=group_index,
        ) from None


def _page_meta(body: Mapping[str, object]) -> tuple[int, bool, int | None]:
    offset = _int(body.get("offset", 0), "pagination_offset_invalid")
    more = _more(body.get("more", 0))
    if not more:
        return offset, False, None
    return offset, True, offset

def _measure_page_meta(
    body: Mapping[str, object], *, requested_offset: int
) -> tuple[int, bool, int | None]:
    parser_stage = "measure_response"
    if (
        isinstance(requested_offset, bool)
        or not isinstance(requested_offset, int)
        or requested_offset < 0
        or requested_offset > _MAX_MEASURE_OFFSET
    ):
        raise _field_error(
            "pagination_requested_offset_invalid",
            parser_stage=parser_stage,
            field_path="request.offset",
            expected_json_type=f"integer in [0, {_MAX_MEASURE_OFFSET}]",
            observed=requested_offset,
        )
    raw_offset = body.get("offset", _MISSING)
    response_offset: int | None = None
    if raw_offset is not _MISSING:
        response_offset = _int(
            raw_offset,
            "pagination_offset_invalid",
            parser_stage=parser_stage,
            field_path="body.offset",
        )
        if response_offset > _MAX_MEASURE_OFFSET:
            raise _field_error(
                "pagination_offset_invalid",
                parser_stage=parser_stage,
                field_path="body.offset",
                expected_json_type=f"integer in [0, {_MAX_MEASURE_OFFSET}]",
                observed=raw_offset,
            )
    raw_more = body.get("more", 0)
    if isinstance(raw_more, bool) or not isinstance(raw_more, int) or raw_more not in (0, 1):
        raise _field_error(
            "pagination_more_invalid",
            parser_stage=parser_stage,
            field_path="body.more",
            expected_json_type="integer 0 or 1",
            observed=raw_more,
        )
    more = bool(raw_more)
    if not more:
        return requested_offset, False, None
    if response_offset is None:
        raise _field_error(
            "pagination_offset_missing",
            parser_stage=parser_stage,
            field_path="body.offset",
            expected_json_type="integer next offset",
        )
    if response_offset <= requested_offset:
        raise _field_error(
            "pagination_did_not_progress",
            parser_stage=parser_stage,
            field_path="body.offset",
            expected_json_type="integer greater than request offset",
            observed=raw_offset,
        )
    return requested_offset, True, response_offset


def parse_measure_response(response: object, *, requested_offset: int = 0) -> MeasurePage:
    body = _body(response, parser_stage="measure_response")
    raw_groups = body.get("measuregrps", [])
    if not isinstance(raw_groups, list):
        raise _field_error(
            "measure_groups_invalid",
            parser_stage="measure_response",
            field_path="body.measuregrps",
            expected_json_type="array",
            observed=raw_groups,
        )
    if len(raw_groups) > WITHINGS_MAX_PAGE_SIZE:
        raise _field_error(
            "measure_groups_limit_exceeded",
            parser_stage="measure_response",
            field_path="body.measuregrps",
            expected_json_type=f"array with at most {WITHINGS_MAX_PAGE_SIZE} entries",
            observed=raw_groups,
        )
    raw_timezone = body.get("timezone", _MISSING)
    body_timezone: str | None = None
    if raw_timezone is not _MISSING and raw_timezone is not None:
        if not isinstance(raw_timezone, str) or not raw_timezone or len(raw_timezone) > 128:
            raise _field_error(
                "measure_timezone_invalid",
                parser_stage="measure_response",
                field_path="body.timezone",
                expected_json_type="timezone string",
                observed=raw_timezone,
            )
        body_timezone = raw_timezone
    groups: list[WithingsMeasureGroup] = []
    for group_index, raw_group in enumerate(raw_groups):
        if not isinstance(raw_group, Mapping):
            raise _field_error(
                "measure_group_invalid",
                parser_stage="measure_response",
                field_path=f"body.measuregrps[{group_index}]",
                expected_json_type="object",
                observed=raw_group,
                group_index=group_index,
            )
        group_id = _int(
            raw_group.get("grpid", _MISSING),
            "measure_group_id_invalid",
            minimum=1,
            parser_stage="measure_response",
            field_path=f"body.measuregrps[{group_index}].grpid",
            group_index=group_index,
        )
        raw_group_timezone = raw_group.get("timezone", _MISSING)
        group_timezone = body_timezone
        if raw_group_timezone is not _MISSING and raw_group_timezone is not None:
            if (
                not isinstance(raw_group_timezone, str)
                or not raw_group_timezone
                or len(raw_group_timezone) > 128
            ):
                raise _field_error(
                    "measure_timezone_invalid",
                    parser_stage="measure_response",
                    field_path=f"body.measuregrps[{group_index}].timezone",
                    expected_json_type="timezone string",
                    observed=raw_group_timezone,
                    group_index=group_index,
                )
            group_timezone = raw_group_timezone
        measured_at = _timestamp(
            raw_group.get("date", _MISSING),
            group_timezone,
            group_index=group_index,
        )
        raw_measures = raw_group.get("measures", [])
        if not isinstance(raw_measures, list):
            raise _field_error(
                "measures_invalid",
                parser_stage="measure_response",
                field_path=f"body.measuregrps[{group_index}].measures",
                expected_json_type="array",
                observed=raw_measures,
                group_index=group_index,
            )
        measures: list[WithingsMeasure] = []
        for measurement_index, raw_measure in enumerate(raw_measures):
            if not isinstance(raw_measure, Mapping):
                raise _field_error(
                    "measure_invalid",
                    parser_stage="measure_response",
                    field_path=(f"body.measuregrps[{group_index}].measures[{measurement_index}]"),
                    expected_json_type="object",
                    observed=raw_measure,
                    group_index=group_index,
                    measurement_index=measurement_index,
                )
            measure_type = raw_measure.get("type", _MISSING)
            if isinstance(measure_type, bool) or not isinstance(measure_type, int):
                raise _field_error(
                    "measure_type_invalid",
                    parser_stage="measure_response",
                    field_path=(
                        f"body.measuregrps[{group_index}].measures[{measurement_index}].type"
                    ),
                    expected_json_type="integer",
                    observed=measure_type,
                    group_index=group_index,
                    measurement_index=measurement_index,
                )
            mapping = WITHINGS_MEASURE_TYPES.get(measure_type)
            if mapping is None:
                continue
            metric_type, unit_name = mapping
            measure_path = f"body.measuregrps[{group_index}].measures[{measurement_index}]"
            original_value = _decimal(
                raw_measure.get("value", _MISSING),
                "measure_value_invalid",
                parser_stage="measure_response",
                field_path=f"{measure_path}.value",
                group_index=group_index,
                measurement_index=measurement_index,
            )
            exponent = _int(
                raw_measure.get("unit", _MISSING),
                "measure_unit_invalid",
                minimum=-1000,
                parser_stage="measure_response",
                field_path=f"{measure_path}.unit",
                group_index=group_index,
                measurement_index=measurement_index,
            )
            try:
                converted = original_value * (Decimal(10) ** Decimal(exponent))
            except InvalidOperation, ValueError, OverflowError:
                raise _field_error(
                    "measure_value_invalid",
                    parser_stage="measure_response",
                    field_path=f"{measure_path}.value",
                    expected_json_type="finite numeric value",
                    observed=raw_measure.get("value", _MISSING),
                    group_index=group_index,
                    measurement_index=measurement_index,
                ) from None
            if not converted.is_finite():
                raise _field_error(
                    "measure_value_invalid",
                    parser_stage="measure_response",
                    field_path=f"{measure_path}.value",
                    expected_json_type="finite numeric value",
                    observed=raw_measure.get("value", _MISSING),
                    group_index=group_index,
                    measurement_index=measurement_index,
                )
            measures.append(
                WithingsMeasure(
                    measure_type,
                    metric_type,
                    unit_name,
                    converted,
                    original_value,
                    exponent,
                )
            )
        groups.append(WithingsMeasureGroup(group_id, measured_at, group_timezone, tuple(measures)))
    offset, more, next_offset = _measure_page_meta(body, requested_offset=requested_offset)
    return MeasurePage(tuple(groups), offset, more, next_offset)


def _civil_date(raw: Mapping[str, object], *, activity_index: int) -> date:
    field_path = f"body.activities[{activity_index}].date"
    raw_date = raw.get("date", _MISSING)
    if raw_date is _MISSING:
        raise _field_error(
            "activity_date_missing",
            parser_stage="activity_response",
            field_path=field_path,
            expected_json_type="string in YYYY-MM-DD format",
        )
    if not isinstance(raw_date, str):
        raise _field_error(
            "activity_date_invalid_type",
            parser_stage="activity_response",
            field_path=field_path,
            expected_json_type="string in YYYY-MM-DD format",
            observed=raw_date,
        )
    try:
        civil_date = date.fromisoformat(raw_date)
    except ValueError:
        raise _field_error(
            "activity_date_invalid",
            parser_stage="activity_response",
            field_path=field_path,
            expected_json_type="string in YYYY-MM-DD format",
            observed=raw_date,
        ) from None
    if civil_date.isoformat() != raw_date:
        raise _field_error(
            "activity_date_invalid",
            parser_stage="activity_response",
            field_path=field_path,
            expected_json_type="string in YYYY-MM-DD format",
            observed=raw_date,
        )
    return civil_date


def parse_activity_response(response: object) -> ActivityPage:
    body = _body(response, parser_stage="activity_response")
    raw_activities = body.get("activities")
    if not isinstance(raw_activities, list):
        raise _invalid("activities_invalid")

    if len(raw_activities) > WITHINGS_MAX_PAGE_SIZE:
        raise _field_error(
            "activity_page_limit_exceeded",
            parser_stage="activity_response",
            field_path="body.activities",
            expected_json_type=f"array with at most {WITHINGS_MAX_PAGE_SIZE} entries",
            observed=raw_activities,
        )
    activities: list[WithingsActivity] = []
    for activity_index, raw_activity in enumerate(raw_activities):
        if not isinstance(raw_activity, Mapping):
            raise _invalid("activity_invalid")
        civil_date = _civil_date(raw_activity, activity_index=activity_index)
        # `date` is already the provider's civil day; timezone is separate metadata.
        raw_timezone = raw_activity.get("timezone", _MISSING)
        if (
            raw_timezone is not _MISSING
            and raw_timezone is not None
            and not isinstance(raw_timezone, str)
        ):
            raise _field_error(
                "activity_timezone_invalid_type",
                parser_stage="activity_response",
                field_path=f"body.activities[{activity_index}].timezone",
                expected_json_type="string or null",
                observed=raw_timezone,
            )
        if "calories" not in raw_activity:
            raise _invalid("activity_calories_missing")
        calories = _decimal(raw_activity["calories"], "activity_calories_invalid")
        if calories < 0:
            raise _invalid("activity_calories_invalid")
        activities.append(WithingsActivity(civil_date, calories))
    offset, more, next_offset = _page_meta(body)
    return ActivityPage(tuple(activities), offset, more, next_offset)


def parse_measure_pages(responses: Sequence[object]) -> tuple[MeasurePage, ...]:
    if len(responses) > WITHINGS_MAX_PAGES:
        raise _field_error(
            "page_limit_exceeded",
            parser_stage="measure_pages",
            field_path="responses",
            expected_json_type=f"array with at most {WITHINGS_MAX_PAGES} entries",
            observed=responses,
        )
    pages: list[MeasurePage] = []
    requested_offset = 0
    record_count = 0
    for response_index, response in enumerate(responses):
        if pages and not pages[-1].more:
            raise _field_error(
                "pagination_unexpected_page",
                parser_stage="measure_pages",
                field_path=f"responses[{response_index}]",
                expected_json_type="no response after final page",
                observed=response,
            )
        page = parse_measure_response(response, requested_offset=requested_offset)
        pages.append(page)
        record_count += len(page.groups)
        if record_count > WITHINGS_MAX_RECORDS:
            raise _field_error(
                "record_limit_exceeded",
                parser_stage="measure_pages",
                field_path="responses",
                expected_json_type=f"at most {WITHINGS_MAX_RECORDS} measurement groups",
                observed=responses,
            )
        if page.more:
            assert page.next_offset is not None
            requested_offset = page.next_offset
    return tuple(pages)


def parse_activity_pages(responses: Sequence[object]) -> tuple[ActivityPage, ...]:
    if len(responses) > WITHINGS_MAX_PAGES:
        raise _invalid("page_limit_exceeded")
    pages: list[ActivityPage] = []
    requested_offset = 0
    record_count = 0
    for response in responses:
        if pages and not pages[-1].more:
            raise _invalid("pagination_unexpected_page")
        page = parse_activity_response(response)
        if page.more:
            next_offset = page.next_offset
            if next_offset is None or next_offset <= requested_offset:
                raise _invalid("pagination_did_not_progress")
            requested_offset = next_offset
        pages.append(page)
        record_count += len(page.activities)
        if record_count > WITHINGS_MAX_RECORDS:
            raise _invalid("record_limit_exceeded")
    return tuple(pages)


__all__ = [
    "ActivityPage",
    "MeasurePage",
    "WithingsActivity",
    "WithingsMeasure",
    "WithingsMeasureGroup",
    "parse_activity_pages",
    "parse_activity_response",
    "parse_measure_pages",
    "parse_measure_response",
]
