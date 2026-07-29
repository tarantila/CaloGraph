# ruff: noqa: E402

import os
from collections.abc import Mapping

from sqlalchemy.engine import make_url

IN_MEMORY_TEST_DATABASE_URL = "sqlite+pysqlite:///:memory:"
POSTGRES_TEST_OPT_IN = "CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS"
SAFE_POSTGRES_TEST_HOSTS = frozenset({"localhost", "127.0.0.1", "postgres-ci"})


def select_test_database_url(environment: Mapping[str, str]) -> str:
    if environment.get(POSTGRES_TEST_OPT_IN) != "1":
        return IN_MEMORY_TEST_DATABASE_URL

    database_url = environment.get("DATABASE_URL", "")
    integration_url = environment.get("CALOGRAPH_POSTGRES_TEST_URL", "")
    if not database_url or database_url != integration_url:
        raise RuntimeError(
            "PostgreSQL test opt-in requires identical DATABASE_URL and "
            "CALOGRAPH_POSTGRES_TEST_URL values"
        )
    parsed = make_url(database_url)
    if (
        not parsed.drivername.startswith("postgresql")
        or parsed.host not in SAFE_POSTGRES_TEST_HOSTS
        or not (parsed.database or "").endswith("_test")
    ):
        raise RuntimeError(
            "PostgreSQL test opt-in requires a local allowlisted host and "
            "a database name ending in _test"
        )
    return database_url


TEST_DATABASE_URL = select_test_database_url(os.environ)

# The unit-test process must never inherit a deployable database or mounted
# runtime secret configuration from the invoking shell/container.
for variable in (
    "DATABASE_PASSWORD",
    "DATABASE_PASSWORD_FILE",
    "SESSION_SECRET_FILE",
    "RATE_LIMIT_SECRET_FILE",
    "CREDENTIAL_ENCRYPTION_KEY",
    "CREDENTIAL_ENCRYPTION_KEY_FILE",
    "MFA_ENCRYPTION_KEY",
    "MFA_ENCRYPTION_KEY_FILE",
):
    os.environ.pop(variable, None)
os.environ["ENVIRONMENT"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SESSION_SECRET"] = "test-session-secret-with-at-least-32-characters"
os.environ["RATE_LIMIT_SECRET"] = "test-rate-limit-secret-with-at-least-32-chars"
os.environ["TRUSTED_HOSTS"] = "testserver,localhost,127.0.0.1"

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import NutritionTarget, TrackingQualitySettings, User

settings.mfa_encryption_key = Fernet.generate_key().decode()


def assert_safe_test_database() -> None:
    if (
        settings.environment != "test"
        or engine.url != make_url(TEST_DATABASE_URL)
    ):
        raise RuntimeError(
            "Refusing destructive test setup outside the selected disposable "
            "test database"
        )


@pytest.fixture(autouse=True)
def clean_database():
    assert_safe_test_database()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db() -> Session:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def user(db: Session) -> User:
    from datetime import date
    from decimal import Decimal

    item = User(
        username="admin",
        password_hash=hash_password("correct-horse-battery-staple"),
        timezone="Europe/Berlin",
    )
    db.add(item)
    db.flush()
    db.add(TrackingQualitySettings(user_id=item.id))
    db.add(
        NutritionTarget(
            user_id=item.id,
            valid_from=date(2024, 1, 1),
            calories_kcal=Decimal("2000"),
            protein_g=Decimal("120"),
        )
    )
    db.commit()
    db.refresh(item)
    return item


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
