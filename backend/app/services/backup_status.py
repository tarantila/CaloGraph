from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

STATUS_SCHEMA_VERSION = 1
_ALLOWED_STATES = {"healthy", "attention", "failed", "unknown", "disabled"}
_ALLOWED_ERROR_CODES = {
    "latest_attempt_failed",
    "verification_failed",
    "report_missing",
    "report_malformed",
}
_ALLOWED_ARCHIVE_RESULTS = {"ARCHIVE_VERIFIED"}
_ALLOWED_RESTORE_RESULTS = {"NEVER_TESTED", "RESTORE_TESTED", "RESTORE_TEST_FAILED"}
_ALLOWED_RESTORE_FAILURE_CODES = {
    "invalid_backup",
    "checksum_mismatch",
    "age_authentication_failed",
    "archive_restore_failed",
    "database_unreachable",
    "schema_check_failed",
    "consistency_check_failed",
    "cleanup_failed",
}
_MAX_STATUS_BYTES = 1024 * 1024
_MAX_RESTORE_TEST_BYTES = 4096


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parsed_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _safe_basename(value: Any, *, extension: str | None = None) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 255:
        return None
    if (
        "/" in value
        or "\\" in value
        or value in {".", ".."}
        or "\x00" in value
        or any(ord(char) < 32 for char in value)
    ):
        return None
    if extension is not None and not value.endswith(extension):
        return None
    return value


def _safe_sha(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) != 64:
        return None
    if not all(char in "0123456789abcdefABCDEF" for char in value):
        return None
    return value.lower()


def _component(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key in ("state", "verification", "encryption", "matching_backup", "off_host_copy", "immutable_copy"):
        item = value.get(key)
        if key == "state":
            if isinstance(item, str) and item in _ALLOWED_STATES:
                result[key] = item
        elif key in {"matching_backup", "off_host_copy", "immutable_copy"}:
            if isinstance(item, bool):
                result[key] = item
        elif isinstance(item, str) and len(item) <= 32 and item in {
            "age", "full", "checksum", "not_verified", "not_reported"
        }:
            result[key] = item
    artifact = _safe_basename(value.get("artifact"))
    if artifact is not None:
        result["_artifact"] = artifact
    checksum = _safe_sha(value.get("sha256"))
    if checksum is not None:
        result["_sha256"] = checksum
    for key in (
        "last_success_at",
        "last_attempt_at",
        "last_verified_at",
        "artifact_created_at",
        "last_restore_test_at",
    ):
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
    if (
        not isinstance(target, str)
        or not target
        or len(target) > 63
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in target)
    ):
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
    for key in ("database", "environment_secrets"):
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


def _read_json_file(path: Path, max_bytes: int) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        raise OSError("status_unavailable")
    with path.open("rb") as handle:
        return json.loads(handle.read(max_bytes + 1))


def _read_verification(path: Path, component: str, now: datetime) -> dict[str, str] | None:
    try:
        raw = _read_json_file(path, _MAX_RESTORE_TEST_BYTES)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != STATUS_SCHEMA_VERSION:
        return None
    if raw.get("result") not in _ALLOWED_ARCHIVE_RESULTS or raw.get("component") != component:
        return None
    artifact = _safe_basename(raw.get("artifact"), extension=".dump.age" if component == "database" else ".tar.age")
    checksum = _safe_sha(raw.get("sha256"))
    verified_at = _timestamp(raw.get("verified_at"))
    if artifact is None or checksum is None or verified_at is None:
        return None
    if _parsed_timestamp(verified_at) > now:
        return None
    return {"artifact": artifact, "sha256": checksum, "verified_at": verified_at}

def _apply_external_verification(status: dict[str, Any], status_path: Path, now: datetime) -> None:
    """Annotate internal components for legacy callers without affecting operation state."""
    del status_path
    for component_name in ("database", "environment_secrets"):
        component = status["components"].get(component_name)
        if not component or component.get("state") != "healthy":
            continue
        verification_path = Path(
            settings.backup_database_verification_status_file
            if component_name == "database"
            else settings.backup_secrets_verification_status_file
        )
        proof = _read_verification(verification_path, component_name, now)
        if proof is None or proof["artifact"] != component.get("_artifact") or proof["sha256"] != component.get("_sha256"):
            continue
        successful_at = component.get("last_success_at")
        if successful_at is not None and _parsed_timestamp(proof["verified_at"]) < _parsed_timestamp(successful_at):
            continue
        component["verification"] = "full"
        component["last_verified_at"] = proof["verified_at"]


def _read_restore_test(path: Path, now: datetime) -> dict[str, Any] | None:
    """Read and sanitize the operator-owned restore-test record."""
    try:
        raw = _read_json_file(path, _MAX_RESTORE_TEST_BYTES)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != STATUS_SCHEMA_VERSION:
        return None
    allowed = {
        "schema_version", "result", "tested_at", "artifact", "sha256", "postgres_major",
        "off_host_copy", "immutable_copy", "failure_code", "last_success_at",
        "last_success_artifact", "last_success_sha256",
    }
    if set(raw) - allowed:
        return None
    result = raw.get("result")
    if result not in _ALLOWED_RESTORE_RESULTS:
        return None
    tested_at = _timestamp(raw.get("tested_at"))
    if tested_at is not None and _parsed_timestamp(tested_at) > now:
        return None
    if result == "NEVER_TESTED":
        if tested_at is None or any(
            key in raw
            for key in (
                "artifact", "sha256", "failure_code", "last_success_at",
                "last_success_artifact", "last_success_sha256",
            )
        ):
            return None
    else:
        if tested_at is None:
            return None
        artifact = _safe_basename(raw.get("artifact"), extension=".dump.age")
        checksum = _safe_sha(raw.get("sha256"))
        if artifact is None or checksum is None:
            return None
        if result == "RESTORE_TESTED" and any(
            key in raw for key in ("last_success_at", "last_success_artifact", "last_success_sha256")
        ):
            return None
    postgres_major = raw.get("postgres_major")
    if postgres_major is not None and (
        not isinstance(postgres_major, int) or isinstance(postgres_major, bool) or not 10 <= postgres_major <= 99
    ):
        return None
    optional_copy_fields: dict[str, bool] = {}
    for key in ("off_host_copy", "immutable_copy"):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, bool):
                return None
            optional_copy_fields[key] = value
    failure_code = raw.get("failure_code")
    if result == "RESTORE_TEST_FAILED":
        if not isinstance(failure_code, str) or failure_code not in _ALLOWED_RESTORE_FAILURE_CODES:
            return None
    elif failure_code is not None:
        return None
    clean: dict[str, Any] = {"schema_version": STATUS_SCHEMA_VERSION, "result": result, "tested_at": tested_at}
    if postgres_major is not None:
        clean["postgres_major"] = postgres_major
    clean.update(optional_copy_fields)
    if failure_code is not None:
        clean["failure_code"] = failure_code
    if result == "RESTORE_TEST_FAILED":
        clean["_artifact"] = artifact
        clean["_sha256"] = checksum
        previous_at = _timestamp(raw.get("last_success_at"))
        previous_artifact = _safe_basename(raw.get("last_success_artifact"), extension=".dump.age")
        previous_sha = _safe_sha(raw.get("last_success_sha256"))
        if previous_at is not None and _parsed_timestamp(previous_at) <= now:
            if previous_artifact is None or previous_sha is None:
                return None
            clean["last_success_at"] = previous_at
            clean["_last_success_artifact"] = previous_artifact
            clean["_last_success_sha256"] = previous_sha
        elif any(key in raw for key in ("last_success_at", "last_success_artifact", "last_success_sha256")):
            return None
    else:
        clean["_artifact"] = artifact if result != "NEVER_TESTED" else None
        clean["_sha256"] = checksum if result != "NEVER_TESTED" else None
        if result == "RESTORE_TESTED":
            clean["last_success_at"] = tested_at
            clean["_last_success_artifact"] = artifact
            clean["_last_success_sha256"] = checksum
    return clean


def _public_status(status: dict[str, Any]) -> dict[str, Any]:
    public = {**status, "components": {}}
    for name, component in status.get("components", {}).items():
        public["components"][name] = {
            key: value for key, value in component.items() if not key.startswith("_")
        }
    return public


def _archive_summary(status: dict[str, Any], now: datetime) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    for component_name in ("database", "environment_secrets"):
        component = status["components"].get(component_name)
        if component_name == "environment_secrets" and not settings.backup_include_secrets:
            summaries[component_name] = {"state": "disabled", "latest_artifact_verified": False}
            continue
        if component is None:
            summaries[component_name] = {"state": "unknown", "latest_artifact_verified": False}
            continue
        if component.get("state") == "disabled":
            summaries[component_name] = {"state": "disabled", "latest_artifact_verified": False}
            continue
        verification_path = Path(
            settings.backup_database_verification_status_file
            if component_name == "database"
            else settings.backup_secrets_verification_status_file
        )
        proof = _read_verification(verification_path, component_name, now)
        successful_at = component.get("last_success_at")
        matching = (
            proof is not None
            and proof["artifact"] == component.get("_artifact")
            and proof["sha256"] == component.get("_sha256")
            and (successful_at is None or _parsed_timestamp(proof["verified_at"]) >= _parsed_timestamp(successful_at))
        )
        if matching and proof is not None:
            summaries[component_name] = {
                "state": "verified", "verified_at": proof["verified_at"], "latest_artifact_verified": True
            }
        else:
            summary: dict[str, Any] = {"state": "not_verified", "latest_artifact_verified": False}
            if proof is not None:
                # Preserve the date of a valid previous check while making it
                # explicit that the currently reported artifact is unverified.
                summary["verified_at"] = proof["verified_at"]
            summaries[component_name] = summary
    return summaries


def _restore_summary(now: datetime) -> dict[str, Any]:
    try:
        record = _read_restore_test(Path(settings.backup_restore_test_status_file), now)
    except (OSError, ValueError, TypeError):
        record = None
    if record is None:
        return {"state": "unknown", "reason": "evidence_unavailable"}
    result = record["result"]
    safe = {key: value for key, value in record.items() if not key.startswith("_") and key != "result"}
    safe["result"] = result
    if result == "NEVER_TESTED":
        return {"state": "never_tested", **safe}
    if result == "RESTORE_TEST_FAILED":
        return {"state": "failed", **safe}
    success_at = _parsed_timestamp(record["last_success_at"])
    interval = settings.backup_restore_test_interval_days * 86400
    state = "current" if (now - success_at).total_seconds() <= interval else "due"
    due_at = success_at.timestamp() + interval
    return {
        "state": state,
        **safe,
        "next_due_at": datetime.fromtimestamp(due_at, UTC).isoformat().replace("+00:00", "Z"),
    }


def _recovery_summary(status: dict[str, Any], now: datetime) -> dict[str, Any]:
    archive = _archive_summary(status, now)
    restore_test = _restore_summary(now)
    archive_states = [item["state"] for item in archive.values()]
    if "not_verified" in archive_states or "unknown" in archive_states:
        archive_state = "attention"
    elif all(item == "disabled" for item in archive.values()):
        archive_state = "disabled"
    else:
        archive_state = "healthy"
    restore_state = restore_test["state"]
    return {
        "overall_state": "attention" if archive_state == "attention" or restore_state in {"due", "failed", "unknown"} else archive_state,
        "archive_verification": {"overall_state": archive_state, "components": archive},
        "restore_test": restore_test,
    }


def _aggregate(status: dict[str, Any], now: datetime) -> tuple[str, list[str]]:
    automation = status["automation"]
    components = status["components"]
    if automation.get("enabled") is False:
        return "disabled", ["deactivated"]
    component_failure = components.get("database", {}).get("state") == "failed"
    secrets = components.get("environment_secrets")
    secrets_failure = settings.backup_include_secrets and secrets is not None and secrets.get("state") == "failed"
    if automation.get("last_error_code") == "latest_attempt_failed" or component_failure or secrets_failure:
        return "failed", ["latest_attempt_failed" if automation.get("last_error_code") == "latest_attempt_failed" else "backup_component_failed"]
    database = components.get("database")
    if database is None:
        return "unknown", ["database_missing"]
    if database.get("state") != "healthy":
        return "unknown", ["database_unavailable"]
    if secrets is None:
        if settings.backup_include_secrets:
            return "unknown", ["secrets_missing"]
        secrets_required = False
    else:
        secrets_required = settings.backup_include_secrets and secrets.get("state") != "disabled"
    if secrets_required and secrets.get("state") != "healthy":
        return "attention", ["secrets_missing"]
    reasons: list[str] = []
    if database.get("matching_backup") is not True or (secrets_required and secrets.get("matching_backup") is not True):
        reasons.append("components_mismatched")
    last_success = automation.get("last_success_at")
    if not last_success:
        reasons.append("backup_missing")
    else:
        parsed = _parsed_timestamp(last_success)
        if parsed > now:
            reasons.append("future_timestamp")
        elif (now - parsed).total_seconds() > status["freshness_threshold_seconds"]:
            reasons.append("stale")
    if automation.get("last_attempt_at") and _parsed_timestamp(automation["last_attempt_at"]) > now:
        reasons.append("future_timestamp")
    for value in (
        status.get("reported_at"),
        automation.get("last_attempt_at"),
        automation.get("last_success_at"),
        *(item.get("last_attempt_at") for item in components.values()),
        *(item.get("last_success_at") for item in components.values()),
        *(item.get("artifact_created_at") for item in components.values()),
    ):
        if value and _parsed_timestamp(value) > now:
            reasons.append("future_timestamp")
            break
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


def _fallback(state: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "overall_state": state,
        "reason_codes": [reason],
        "freshness_threshold_seconds": settings.backup_freshness_threshold_seconds,
        "automation": _configured_automation(state != "disabled"),
        "recovery": {
            "overall_state": "unknown",
            "archive_verification": {"overall_state": "unknown", "components": {}},
            "restore_test": {"state": "unknown", "reason": "evidence_unavailable"},
        },
    }


def read_backup_status(now: datetime | None = None) -> dict[str, Any]:
    """Return the public backup operation and independent recovery contract."""
    if not settings.backup_agent_enabled:
        return _fallback("disabled", "deactivated")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        path = Path(settings.backup_status_file)
        status = _safe_status(_read_json_file(path, _MAX_STATUS_BYTES))
        reported = _parsed_timestamp(status["reported_at"])
        if (current - reported).total_seconds() > settings.backup_status_max_age_seconds:
            public = _public_status(status)
            return {
                **public,
                "overall_state": "unknown",
                "reason_codes": ["report_expired"],
                "recovery": _recovery_summary(status, current),
            }
        state, reasons = _aggregate(status, current)
        status["overall_state"] = state
        status["reason_codes"] = reasons
        status["recovery"] = _recovery_summary(status, current)
        return _public_status(status)
    except OSError:
        return _fallback("unknown", "report_missing")
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Backup status malformed: %s", type(exc).__name__)
        return _fallback("unknown", "report_malformed")
