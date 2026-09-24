from __future__ import annotations

from datetime import date

import app.api.google_health as google_health_api
from app.config import settings
from app.services.google_health_sync import (
    GoogleHealthDomainDiagnostic,
    GoogleHealthDomainResult,
)


def test_sync_api_exposes_bounded_parser_diagnostic(client, user, monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_health_enabled", True)
    csrf_response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    csrf = csrf_response.json()["csrf_token"]
    diagnostic = GoogleHealthDomainDiagnostic(
        domain="nutrition",
        operation="nutrition_read",
        endpoint_key="nutrition_log_data_points",
        parser_stage="nutrition_data_point",
        structural_reason_code="invalid_field",
        error_category="invalid_response",
        upstream_status_code=200,
        retryable=False,
        reauth_required=False,
        page_index=1,
        point_index=2,
        field_path="dataPoints[2].nutritionLog.interval",
        validation_rule="interval_object",
        numeric_reason_code=None,
        observed_json_type="string",
        expected_json_type="object",
    )
    domain = GoogleHealthDomainResult(
        status="failed",
        fetched_count=0,
        persisted_count=0,
        requested_start=date(2026, 1, 1),
        requested_end=date(2026, 1, 30),
        error_code="invalid_response",
        diagnostic=diagnostic,
    )

    class FakeService:
        def __init__(self, **kwargs):
            del kwargs

        def sync(self, **kwargs):
            del kwargs
            return type(
                "Result",
                (),
                {
                    "status": "partial_failure",
                    "nutrition": domain,
                    "activity_energy": domain,
                    "weight": domain,
                },
            )()

    monkeypatch.setattr(google_health_api, "GoogleHealthSyncService", FakeService)
    response = client.post("/api/v1/google-health/sync", headers={"X-CSRF-Token": csrf})

    payload = response.json()
    diagnostic_payload = payload["nutrition"]["diagnostic"]
    assert set(diagnostic_payload) <= {
        "domain",
        "operation",
        "endpoint_key",
        "parser_stage",
        "structural_reason_code",
        "error_category",
        "upstream_status_code",
        "retryable",
        "reauth_required",
        "chunk_index",
        "page_index",
        "point_index",
        "field_path",
        "validation_rule",
        "numeric_reason_code",
        "observed_json_type",
        "expected_json_type",
    }
    assert diagnostic_payload["page_index"] == 1
    assert diagnostic_payload["point_index"] == 2
    assert diagnostic_payload["field_path"] == "dataPoints[2].nutritionLog.interval"
    assert "refresh-token" not in response.text
    assert "9876.54321" not in response.text
    assert "raw-provider-payload" not in response.text
