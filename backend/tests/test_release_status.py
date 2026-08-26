from __future__ import annotations

import io
import json
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import pytest

from app.services import release_status


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._body = io.StringIO(json.dumps(payload))

    def __enter__(self) -> _Response:
        return self

    def read(self, size: int = -1) -> str:
        return self._body.read(size)

    def __exit__(self, *_: object) -> None:
        self._body.close()


def _release(tag_name: str = "v0.5.0") -> _Response:
    return _Response(
        200,
        {
            "tag_name": tag_name,
            "html_url": f"https://github.com/tarantila/CaloGraph/releases/tag/{tag_name}",
            "published_at": "2026-08-24T00:00:00Z",
        },
    )


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    release_status.clear_release_status_cache()


@pytest.mark.parametrize(
    ("running", "latest", "expected"),
    [
        ("0.5.0", "v0.5.0", "current"),
        ("0.4.2", "v0.5.0", "update_available"),
        ("0.5.0", "v0.4.2", "development"),
    ],
)
def test_compares_semver_release_statuses(running: str, latest: str, expected: str) -> None:
    with patch("app.services.release_status.urlopen", return_value=_release(latest)):
        result = release_status.get_release_status(running)

    assert result["running"] == running
    assert result["latest"] == latest.removeprefix("v")
    assert result["status"] == expected
    assert result["release_url"] == f"https://github.com/tarantila/CaloGraph/releases/tag/{latest}"


@pytest.mark.parametrize(
    "response",
    [
        TimeoutError(),
        HTTPError(release_status.GITHUB_LATEST_RELEASE_URL, 403, "rate limited", None, None),
        HTTPError(release_status.GITHUB_LATEST_RELEASE_URL, 500, "error", None, None),
        _Response(200, {"tag_name": "invalid", "html_url": "https://example.invalid/release"}),
    ],
)
def test_external_release_failures_are_reported_as_unknown(response: object) -> None:
    if isinstance(response, _Response):
        mock = Mock(return_value=response)
    else:
        mock = Mock(side_effect=response)
    with patch("app.services.release_status.urlopen", mock):
        result = release_status.get_release_status("0.5.0")

    assert result["status"] == "unknown"
    assert result["latest"] is None
    assert result["release_url"] is None


def test_release_status_uses_in_process_cache() -> None:
    request = Mock(return_value=_release("v0.4.2"))
    with patch("app.services.release_status.urlopen", request):
        first = release_status.get_release_status("0.5.0")
        second = release_status.get_release_status("0.5.0")

    assert first == second
    request.assert_called_once()

def test_disabled_release_status_does_not_contact_github() -> None:
    with patch("app.services.release_status.urlopen") as request:
        result = release_status.get_release_status("0.5.0", enabled=False)

    request.assert_not_called()
    assert result["status"] == "unknown"
    assert result["latest"] is None


def test_admin_system_endpoint_returns_release_status_and_stays_admin_only(
    client,
    user,
    db,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.api.admin.get_release_status",
        lambda running, **_: {
            "running": running,
            "latest": "0.4.2",
            "status": "development",
            "release_url": "https://github.com/tarantila/CaloGraph/releases/tag/v0.4.2",
            "checked_at": "2026-08-24T00:00:00+00:00",
        },
    )
    user.is_admin = True
    db.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200

    response = client.get("/api/v1/admin/system")
    assert response.status_code == 200
    assert response.json()["version"]["status"] == "development"

    user.is_admin = False
    db.commit()
    assert client.get("/api/v1/admin/system").status_code == 403
