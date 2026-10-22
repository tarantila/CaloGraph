# ruff: noqa: E402

import builtins
import importlib
import json
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

settings.credential_encryption_key = Fernet.generate_key().decode()
settings.mfa_encryption_key = Fernet.generate_key().decode()
settings.yazio_enabled = True


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
    with engine.connect() as connection:
        sqlite_foreign_keys = engine.dialect.name == "sqlite"
        if sqlite_foreign_keys:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        Base.metadata.drop_all(connection)
        if sqlite_foreign_keys:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
        Base.metadata.create_all(connection)
        connection.commit()
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


@pytest.fixture(params=["days.json", "nutrients.json"])
def compatible_yazio_file(request: pytest.FixtureRequest) -> dict[str, object]:
    from decimal import Decimal

    if request.param == "days.json":
        payload = {
            "weight": {"2026-07-20": {"value": 72.5, "unit": "kg"}},
            "2026-07-20": {
                "daily_summary": {
                    "activity_energy": 300,
                    "steps": 8000,
                    "water_intake": 2000,
                    "units": {"unit_energy": "kcal"},
                    "meals": {
                        "breakfast": {
                            "nutrients": {
                                "energy.energy": 450,
                                "nutrient.protein": 25,
                                "nutrient.carb": 60,
                                "nutrient.fat": 15,
                            }
                        }
                    },
                }
            },
        }
        values = {
            "dietary_energy_kcal": Decimal("450"),
            "protein_g": Decimal("25"),
            "carbohydrates_g": Decimal("60"),
            "fat_g": Decimal("15"),
            "active_energy_kcal": Decimal("300"),
            "weight_kg": Decimal("72.5"),
        }
        units = {
            "dietary_energy_kcal": "kcal",
            "protein_g": "g",
            "carbohydrates_g": "g",
            "fat_g": "g",
            "active_energy_kcal": "kcal",
            "weight_kg": "kg",
        }
    else:
        payload = {
            "vitamin.d": {"2026-07-20": 0.0000125},
            "mineral.iron": {"2026-07-20": 0.0084},
        }
        values = {"vitamin_d_ug": Decimal("12.5"), "iron_mg": Decimal("8.4")}
        units = {"vitamin_d_ug": "ug", "iron_mg": "mg"}
    return {
        "filename": request.param,
        "payload": payload,
        "raw": json.dumps(payload).encode(),
        "values": values,
        "units": units,
    }


@pytest.fixture
def block_yazio_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import yazio_transport

    original_import = builtins.__import__
    original_import_module = importlib.import_module

    def deny_exporter(name: str) -> None:
        if name == "yazio_exporter" or name.startswith("yazio_exporter."):
            pytest.fail("Compatible file imports must not import yazio_exporter")

    def guarded_import(name, *args, **kwargs):
        deny_exporter(name)
        return original_import(name, *args, **kwargs)

    def guarded_import_module(name, *args, **kwargs):
        deny_exporter(name)
        return original_import_module(name, *args, **kwargs)

    def forbidden_runtime(*args, **kwargs):
        pytest.fail("Compatible file imports must not decrypt credentials or invoke SDK transport")

    # Guard import statements and dynamic imports, even for an installed/cached package.
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(importlib, "import_module", guarded_import_module)
    # Both SDK credential validation and domain fetching cross this worker boundary.
    monkeypatch.setattr(yazio_transport, "_run_worker", forbidden_runtime)
    monkeypatch.setattr(Fernet, "decrypt", forbidden_runtime)


@pytest.fixture(params=["unconfigured", "configured"])
def yazio_file_source_identifier(
    request: pytest.FixtureRequest, db: Session, user: User
) -> str:
    from app.models import YazioConnection

    if request.param == "unconfigured":
        return "yazio-account"
    identifier = "yazio:synthetic-compatible-file-account"
    db.add(
        YazioConnection(
            user_id=user.id,
            encrypted_email=b"synthetic-opaque-email-not-a-fernet-token",
            encrypted_password=b"synthetic-opaque-password-not-a-fernet-token",
            source_identifier=identifier,
        )
    )
    db.commit()
    return identifier
