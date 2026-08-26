from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

PROBLEM_BASE = "urn:calograph:problem:"
CSRF_VALIDATION_FAILED = f"{PROBLEM_BASE}csrf-validation-failed"
INVALID_REQUEST_ORIGIN = f"{PROBLEM_BASE}invalid-request-origin"
INVALID_CREDENTIALS = f"{PROBLEM_BASE}invalid-credentials"
INVALID_INVITATION = f"{PROBLEM_BASE}invalid-invitation"
USERNAME_TAKEN = f"{PROBLEM_BASE}username-taken"
INVALID_MFA = f"{PROBLEM_BASE}invalid-mfa-code"
VALIDATION_ERROR = f"{PROBLEM_BASE}validation-error"
INVALID_TIMEZONE = f"{PROBLEM_BASE}invalid-timezone"
RATE_LIMITED = f"{PROBLEM_BASE}rate-limited"
ADMIN_REAUTH_FAILED = f"{PROBLEM_BASE}admin-reauthentication-failed"
ADMIN_REQUIRED = f"{PROBLEM_BASE}admin-required"
USER_NOT_FOUND = f"{PROBLEM_BASE}user-not-found"
USER_SELF_ACTION = f"{PROBLEM_BASE}user-self-action"
LAST_ADMIN = f"{PROBLEM_BASE}last-admin"
TARGET_ACTIVE = f"{PROBLEM_BASE}target-active"
USER_OPERATION_BUSY = f"{PROBLEM_BASE}user-operation-busy"
TARGET_CONFIRMATION = f"{PROBLEM_BASE}target-confirmation"
INVALID_CURRENT_PASSWORD = f"{PROBLEM_BASE}invalid-current-password"
PASSWORD_POLICY = f"{PROBLEM_BASE}password-policy"
LAST_TARGET_REQUIRED = f"{PROBLEM_BASE}last-target-required"
TARGET_VERSION_NOT_FOUND = f"{PROBLEM_BASE}target-version-not-found"
ACTIVITY_SOURCE_UNAVAILABLE = f"{PROBLEM_BASE}activity-source-unavailable"
DATA_EXPORT_BUSY = f"{PROBLEM_BASE}data-export-busy"


class ProblemHTTPException(HTTPException):
    def __init__(
        self,
        status_code: int,
        detail: Any,
        problem_type: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.problem_type = problem_type
        super().__init__(status_code=status_code, detail=detail, headers=headers)


def problem_exception(
    status_code: int,
    detail: Any,
    problem_type: str,
    headers: Mapping[str, str] | None = None,
) -> ProblemHTTPException:
    return ProblemHTTPException(status_code, detail, problem_type, headers)
