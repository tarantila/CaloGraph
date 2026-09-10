from __future__ import annotations

import hashlib
import json

from .contracts import ProjectionInputManifest
from .tokens import TechnicalEvidenceToken


def _token_payload(token: TechnicalEvidenceToken) -> dict[str, object]:
    return token.canonical_payload()


def compute_input_watermark(manifest: ProjectionInputManifest) -> str:
    policy = manifest.policy
    policy_payload: dict[str, object] | None
    if policy is None:
        policy_payload = None
    else:
        policy_payload = {
            "policy_id": str(policy.policy_id),
            "user_id": str(policy.user_id),
            "version": policy.version,
            "effective_from": policy.effective_from.isoformat(),
        }

    payload = {
        "watermark_format_version": manifest.watermark_format_version,
        "user_id": str(manifest.user_id),
        "local_date": manifest.local_date.isoformat(),
        "projection_algorithm_version": manifest.projection_algorithm_version,
        "metric_registry_version": manifest.metric_registry_version,
        "policy": policy_payload,
        "relevant_rules": [
            {
                "rule_id": str(rule.rule_id),
                "data_area": rule.data_area,
                "metric_key": rule.metric_key,
                "provider_key": rule.provider_key,
                "priority_rank": rule.priority_rank,
            }
            for rule in sorted(
                manifest.relevant_rules,
                key=lambda rule: (
                    str(rule.rule_id),
                    rule.data_area,
                    rule.metric_key or "",
                    rule.provider_key,
                    rule.priority_rank,
                ),
            )
        ],
        "technical_evidence": [
            _token_payload(token)
            for token in sorted(
                manifest.technical_evidence,
                key=lambda token: (
                    token.kind,
                    str(token.token_id),
                    json.dumps(
                        _token_payload(token),
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                ),
            )
        ],
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"
