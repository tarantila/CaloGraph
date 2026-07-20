import io
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from defusedxml.common import DefusedXmlException

from app.importers.apple_xml import parse_apple_health_xml
from app.importers.common import CanonicalSample, local_date_for, normalize_value
from app.importers.json_adapter import parse_json_payload


def test_unit_conversions() -> None:
    assert normalize_value(Decimal("418.4"), "kJ", "kcal") == Decimal("100")
    assert normalize_value(Decimal("1"), "lb", "kg") == Decimal("0.45359237")
    assert normalize_value(Decimal("2"), "L", "ml") == Decimal("2000")


def test_berlin_dst_and_midnight_local_date() -> None:
    before_switch = datetime(2024, 3, 30, 23, 30, tzinfo=UTC)
    after_switch = datetime(2024, 3, 31, 22, 30, tzinfo=UTC)
    assert str(local_date_for(before_switch, "Europe/Berlin")) == "2024-03-31"
    assert str(local_date_for(after_switch, "Europe/Berlin")) == "2024-04-01"


def test_fingerprint_is_deterministic() -> None:
    user_id = uuid4()
    sample = CanonicalSample(
        metric_type="protein_g",
        value=Decimal("42.0"),
        unit="g",
        original_value=Decimal("42000"),
        original_unit="mg",
        start_at=datetime(2024, 1, 1, tzinfo=UTC),
        end_at=datetime(2024, 1, 1, tzinfo=UTC),
        timezone="Europe/Berlin",
        source_type="test",
        source_name="Test",
        source_identifier="source",
        external_sample_id=None,
    )
    assert sample.fingerprint(user_id) == sample.fingerprint(user_id)


def test_health_auto_export_and_unknown_type() -> None:
    result = parse_json_payload(
        {
            "data": {
                "metrics": [
                    {
                        "name": "dietary_protein",
                        "units": "g",
                        "data": [{"qty": 30, "date": "2024-02-06 14:30:00 +0100"}],
                    },
                    {
                        "name": "unknown_private_metric",
                        "units": "x",
                        "data": [{"qty": 1, "date": "2024-02-06 14:30:00 +0100"}],
                    },
                ]
            }
        },
        "Europe/Berlin",
    )
    assert result.received == 2
    assert len(result.samples) == 1
    assert result.unknown_types == {"unknown_private_metric"}


def test_xml_entities_are_rejected() -> None:
    xml = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY x "boom">]><HealthData><Record type="HKQuantityTypeIdentifierDietaryProtein" value="&x;" unit="g" startDate="2024-01-01 10:00:00 +0100" /></HealthData>'
    with pytest.raises(DefusedXmlException):
        parse_apple_health_xml(io.BytesIO(xml), "Europe/Berlin")
