from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings

_ALLOWED_ERROR_CODES = {
    "latest_attempt_failed",
    "verification_failed",
    "report_missing",
    "report_malformed",
}
logger = logging.getLogger(__name__)

STATUS_SCHEMA_VERSION = 1
_ALLOWED_STATES = {"healthy", "attention", "failed", "unknown", "disabled"}
_MAX_STATUS_BYTES = 1024 * 1024


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _component(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key in ("state", "verification", "encryption", "matching_backup", "off_host_copy", "immutable_copy"):
        item = value.get(key)
        if key == "state":
            if isinstance(item, str) and item in _ALLOWED_STATES:
                result[key] = item
        elif key == "matching_backup" or key in {"off_host_copy", "immutable_copy"}:
            if isinstance(item, bool):
                result[key] = item
        elif isinstance(item, str) and len(item) <= 32 and item in {"age", "full", "checksum", "not_verified", "not_reported"}:
            result[key] = item
    for key in ("last_success_at", "last_attempt_at", "last_verified_at", "artifact_created_at", "last_restore_test_at"):
        value_timestamp = _timestamp(value.get(key))
        if value_timestamp is not None:
            result[key] = value_timestamp
    age = value.get("age_seconds")
    if isinstance(age, (int, float)) and not isinstance(age, bool) and 0 <= age <= 31_536_000:
        result["age_seconds"] = int(age)
    return result


def _safe_status(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("schema_version") != STATUS_SCHEMA_VERSION:
        raise ValueError("invalid_schema")
    reported_at = _timestamp(raw.get("reported_at"))
    if reported_at is None:
        raise ValueError("invalid_reported_at")
    target = raw.get("target", "calograph")
    if not isinstance(target, str) or not target or len(target) > 63 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in target):
        raise ValueError("invalid_target")
    freshness = raw.get("freshness_threshold_seconds", settings.backup_freshness_threshold_seconds)
    if not isinstance(freshness, int) or isinstance(freshness, bool) or not 60 <= freshness <= 31_536_000:
        raise ValueError("invalid_freshness_threshold")
    automation_raw = raw.get("automation")
    if not isinstance(automation_raw, dict) or not isinstance(automation_raw.get("enabled"), bool):
        raise ValueError("invalid_automation")
    automation: dict[str, Any] = {"enabled": automation_raw["enabled"]}
    for key in ("last_attempt_at", "last_success_at", "next_run_at"):
        value = _timestamp(automation_raw.get(key))
        if value is not None:
            automation[key] = value
    value = automation_raw.get("last_error_code")
    if value is None:
        automation["last_error_code"] = None
    elif isinstance(value, str) and value in _ALLOWED_ERROR_CODES:
        automation["last_error_code"] = value
    else:
        raise ValueError("invalid_error_code")
    for key in ("schedule_timezone", "schedule_time"):
        value = automation_raw.get(key)
        if (
            isinstance(value, str)
            and len(value) <= 64
            and all(char not in value for char in "\r\n")
            and not value.startswith("/")
            and ".." not in value
        ):
            automation[key] = value
    retention = automation_raw.get("retention_days")
    if isinstance(retention, int) and not isinstance(retention, bool) and 1 <= retention <= 3650:
        automation["retention_days"] = retention
    components_raw = raw.get("components")
    if not isinstance(components_raw, dict):
        raise ValueError("missing_components")
    components: dict[str, Any] = {}
    for key in ("database", "environment_secrets", "restore_test"):
        component = _component(components_raw.get(key))
        if component is not None:
            components[key] = component
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "reported_at": reported_at,
        "target": target,
        "freshness_threshold_seconds": freshness,
        "automation": automation,
        "components": components,
    }



def _aggregate(status: dict[str, Any], now: datetime) -> tuple[str, list[str]]:
    automation = status["automation"]
    components = status["components"]
    if automation.get("enabled") is False:
        return "disabled", ["deactivated"]
    reasons: list[str] = []
    if automation.get("last_error_code") or any(item.get("state") == "failed" for item in components.values()):
        reasons.append("latest_attempt_failed" if automation.get("last_error_code") else "verification_failed")
        return "failed", reasons
    database = components.get("database")
    secrets = components.get("environment_secrets")
    if database is None:
        return "unknown", ["database_missing"]
    secrets_required = secrets is None or secrets.get("state") != "disabled"
    if secrets is None and secrets_required:
        return "unknown", ["secrets_missing"]
    if secrets_required and secrets is not None and (
        secrets.get("state") != "healthy" or not secrets.get("last_success_at")
    ):
        reasons.append("secrets_missing")
    if database.get("matching_backup") is not True or (
        secrets_required and secrets is not None and secrets.get("matching_backup") is not True
    ):
        reasons.append("components_mismatched")
    if database.get("verification") != "full":
        reasons.append("verification_missing")
    if secrets_required and secrets is not None and secrets.get("verification") != "full":
        reasons.append("verification_missing")
    last_success = automation.get("last_success_at")
    if not last_success:
        reasons.append("backup_missing")
    else:
        parsed = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
        if parsed > now:
            reasons.append("future_timestamp")
        elif (now - parsed).total_seconds() > status["freshness_threshold_seconds"]:
            reasons.append("stale")
    restore_test = components.get("restore_test")
    if restore_test and restore_test.get("state") == "attention":
        reasons.append("restore_test_overdue")
    if reasons:
        return "attention", list(dict.fromkeys(reasons))
    return "healthy", []


def _configured_automation(enabled: bool) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "schedule_timezone": settings.calograph_timezone,
        "schedule_time": settings.backup_schedule_time,
        "retention_days": settings.backup_retention_days,
    }


def read_backup_status(now: datetime | None = None) -> dict[str, Any]:
    """Return only the versioned, public-safe backup status contract."""
    if not settings.backup_agent_enabled:
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "overall_state": "disabled",
            "reason_codes": ["deactivated"],
            "freshness_threshold_seconds": settings.backup_freshness_threshold_seconds,
            "automation": _configured_automation(False),
        }
    try:
        path = Path(settings.backup_status_file)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_STATUS_BYTES:
            raise OSError("report_missing")
        with path.open("rb") as handle:
            raw = json.loads(handle.read(_MAX_STATUS_BYTES + 1))
        status = _safe_status(raw)
        current = (now or datetime.now(UTC)).astimezone(UTC)
        reported = datetime.fromisoformat(status["reported_at"].replace("Z", "+00:00"))
        if (current - reported).total_seconds() > settings.backup_status_max_age_seconds:
            return {
                "schema_version": STATUS_SCHEMA_VERSION,
                "overall_state": "unknown",
                "reason_codes": ["report_expired"],
                "reported_at": status["reported_at"],
                "freshness_threshold_seconds": status["freshness_threshold_seconds"],
                "automation": status["automation"],
            }
        state, reasons = _aggregate(status, current)
        return {**status, "overall_state": state, "reason_codes": reasons}
    except OSError:
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "overall_state": "unknown",
            "reason_codes": ["report_missing"],
            "freshness_threshold_seconds": settings.backup_freshness_threshold_seconds,
            "automation": _configured_automation(True),
        }
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Backup status malformed: %s", type(exc).__name__)
        return {
            "schema_version": STATUS_SCHEMA_VERSION,
            "overall_state": "unknown",
            "reason_codes": ["report_malformed"],
            "freshness_threshold_seconds": settings.backup_freshness_threshold_seconds,
            "automation": _configured_automation(True),
        }
