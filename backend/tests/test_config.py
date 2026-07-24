import pytest
from pydantic import ValidationError

from app.config import Settings


def test_public_url_is_canonical_and_extends_request_allowlists() -> None:
    configured = Settings(
        _env_file=None,
        calograph_public_url="https://nutrition.example.test/",
        trusted_hosts="localhost",
        trusted_origins="http://localhost:8180",
        trusted_proxy_networks="172.18.0.0/16,127.0.0.1",
    )

    assert configured.calograph_public_url == "https://nutrition.example.test"
    assert configured.trusted_host_list == ["localhost", "nutrition.example.test"]
    assert configured.trusted_origin_list == [
        "http://localhost:8180",
        "https://nutrition.example.test",
    ]
    assert configured.trusted_proxy_networks == "172.18.0.0/16,127.0.0.1"


@pytest.mark.parametrize(
    "value",
    [
        "nutrition.example.test",
        "ftp://nutrition.example.test",
        "https://user:password@nutrition.example.test",
        "https://nutrition.example.test/subpath",
    ],
)
def test_public_url_rejects_non_origin_values(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, calograph_public_url=value)


def test_proxy_networks_reject_wildcard_trust() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, trusted_proxy_networks="*")
