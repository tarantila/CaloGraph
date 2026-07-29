import os

import conftest
import pytest

from app.config import settings
from app.database import engine


def test_unit_suite_is_pinned_to_disposable_in_memory_database() -> None:
    assert os.environ["ENVIRONMENT"] == "test"
    assert os.environ["DATABASE_URL"] == conftest.TEST_DATABASE_URL
    assert settings.environment == "test"
    assert engine.url == conftest.make_url(conftest.TEST_DATABASE_URL)


def test_destructive_setup_guard_rejects_non_test_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "environment", "production")

    with pytest.raises(RuntimeError, match="Refusing destructive test setup"):
        conftest.assert_safe_test_database()


@pytest.mark.parametrize(
    "environment",
    [
        {
            conftest.POSTGRES_TEST_OPT_IN: "1",
            "DATABASE_URL": (
                "postgresql+psycopg://calograph:secret@production.internal/calograph"
            ),
            "CALOGRAPH_POSTGRES_TEST_URL": (
                "postgresql+psycopg://calograph:secret@production.internal/calograph"
            ),
        },
        {
            conftest.POSTGRES_TEST_OPT_IN: "1",
            "DATABASE_URL": (
                "postgresql+psycopg://calograph:secret@localhost/calograph_test"
            ),
            "CALOGRAPH_POSTGRES_TEST_URL": (
                "postgresql+psycopg://other:secret@localhost/calograph_test"
            ),
        },
    ],
)
def test_postgres_test_opt_in_rejects_unsafe_targets(
    environment: dict[str, str],
) -> None:
    with pytest.raises(RuntimeError, match="PostgreSQL test opt-in"):
        conftest.select_test_database_url(environment)


def test_postgres_test_opt_in_accepts_only_local_test_database() -> None:
    database_url = (
        "postgresql+psycopg://calograph_test:secret@localhost/calograph_test"
    )

    assert (
        conftest.select_test_database_url(
            {
                conftest.POSTGRES_TEST_OPT_IN: "1",
                "DATABASE_URL": database_url,
                "CALOGRAPH_POSTGRES_TEST_URL": database_url,
            }
        )
        == database_url
    )
