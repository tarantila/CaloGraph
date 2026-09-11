from __future__ import annotations

from datetime import datetime

from app.google_health.client import GoogleHealthClient


class Credentials:
    valid = True
    expired = False
    token = "token"


class Response:
    status_code = 200
    headers = {"content-type": "application/json"}

    def json(self) -> object:
        return {
            "dataPoints": [
                {
                    "name": "users/u/dataTypes/nutrition-log/dataPoints/point-1",
                    "nutritionLog": {
                        "interval": {
                            "startTime": "2026-01-01T08:00:00Z",
                            "endTime": "2026-01-01T08:30:00Z",
                            "startUtcOffset": "0s",
                            "endUtcOffset": "0s",
                            "civilStartTime": {
                                "date": {"year": 2026, "month": 1, "day": 1},
                                "time": {"hours": 8, "minutes": 0, "seconds": 0, "nanos": 0},
                            },
                            "civilEndTime": {
                                "date": {"year": 2026, "month": 1, "day": 1},
                                "time": {"hours": 8, "minutes": 30, "seconds": 0, "nanos": 0},
                            },
                        },
                        "foodDisplayName": "Oats",
                    },
                }
            ],
            "nextPageToken": "next",
        }


class Transport:
    def __init__(self) -> None:
        self.response = Response()
        self.calls: list[dict[str, object]] = []

    def get_nutrition_log(self, **kwargs: object) -> Response:
        self.calls.append(kwargs)
        return self.response


def test_page_is_memory_only_and_aggregate_exposes_safe_metadata() -> None:
    transport = Transport()
    page = GoogleHealthClient(transport, Credentials()).get_nutrition_log_page(page_size=10)

    aggregate = page.aggregate()

    assert aggregate.count == 1
    assert aggregate.next_page_token == "next"
    assert aggregate.page_size == 10
    assert aggregate.page_token is None
    assert aggregate.start_time is None
    assert aggregate.end_time is None
    assert not hasattr(aggregate, "payload")
    assert "rawSecret" not in repr(aggregate)


def test_page_does_not_call_hydration_or_domain_persistence_paths(monkeypatch) -> None:
    transport = Transport()
    hydration_calls: list[object] = []
    orm_calls: list[object] = []

    monkeypatch.setattr(
        "app.google_health.client.HydrationLog",
        lambda *_args, **_kwargs: hydration_calls.append(True),
        raising=False,
    )
    monkeypatch.setattr(
        "app.google_health.client.NutritionLogRepository",
        lambda *_args, **_kwargs: orm_calls.append(True),
        raising=False,
    )

    page = GoogleHealthClient(transport, Credentials()).get_nutrition_log_page(page_size=10)

    assert page.data_points[0].nutrition_log.interval.start_time == datetime(2026, 1, 1, 8, tzinfo=__import__("datetime").UTC)
    assert hydration_calls == []
    assert orm_calls == []
    assert not hasattr(transport, "persist")
    assert not hasattr(transport, "create_hydration")
