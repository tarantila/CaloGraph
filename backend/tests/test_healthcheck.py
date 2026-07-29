from unittest.mock import MagicMock

from app.healthcheck import check_liveness


def test_liveness_check_uses_the_configured_public_host(monkeypatch) -> None:
    response = MagicMock()
    response.status = 200
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    captured = {}

    def urlopen(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr("urllib.request.urlopen", urlopen)

    check_liveness("https://nutrition.calograph.de")

    assert captured["request"].full_url == "http://127.0.0.1:8000/health/live"
    assert captured["request"].get_header("Host") == "nutrition.calograph.de"
    assert captured["timeout"] == 3
