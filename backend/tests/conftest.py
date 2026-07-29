import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault(
    "SESSION_SECRET",
    "test-session-secret-with-at-least-32-characters",
)
os.environ.setdefault(
    "RATE_LIMIT_SECRET",
    "test-rate-limit-secret-with-at-least-32-chars",
)
os.environ.setdefault("TRUSTED_HOSTS", "testserver,localhost,127.0.0.1")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.database import Base, SessionLocal, engine
from app.main import app
from app.models import NutritionTarget, TrackingQualitySettings, User


@pytest.fixture(autouse=True)
def clean_database():
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
