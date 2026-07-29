import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database import engine as application_engine
from app.models import RateLimitBucket
from app.services.import_guard import ImportAlreadyRunning, import_slot
from app.services.rate_limit import (
    check_rate_limit,
    clear_rate_limit,
    hash_rate_limit_key,
)
from app.services.yazio_guard import YazioOperationBusy, yazio_operation_slot


@pytest.mark.skipif(
    not os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_application_guards_are_exercised_with_postgres() -> None:
    assert application_engine.dialect.name == "postgresql"


@pytest.mark.skipif(
    not os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_concurrent_rate_limit_updates_are_not_lost() -> None:
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    action = "test-concurrency"
    key = f"test:{uuid.uuid4()}"
    attempts = 40

    def consume(_: int) -> None:
        with sessions() as db:
            check_rate_limit(db, action, key, limit=attempts + 1, window_seconds=300)

    try:
        with ThreadPoolExecutor(max_workers=20) as executor:
            list(executor.map(consume, range(attempts)))

        with Session(engine) as db:
            count = db.scalar(
                select(func.sum(RateLimitBucket.count)).where(
                    RateLimitBucket.key_hash == hash_rate_limit_key(key),
                    RateLimitBucket.action == action,
                )
            )
            assert count == attempts
    finally:
        with Session(engine) as db:
            clear_rate_limit(db, action, key)
        engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_postgres_import_slot_is_exclusive_and_released() -> None:
    user_id = uuid.uuid4()

    with import_slot(user_id), pytest.raises(
        ImportAlreadyRunning
    ), import_slot(user_id):
        pass

    with import_slot(user_id):
        pass


@pytest.mark.skipif(
    not os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"),
    reason="PostgreSQL integration URL is not configured",
)
def test_postgres_yazio_slots_enforce_user_and_global_capacity(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "yazio_max_parallel_operations", 2)

    with yazio_operation_slot("user-1"):
        with pytest.raises(YazioOperationBusy), yazio_operation_slot("user-1"):
            pass
        with (
            yazio_operation_slot("user-2"),
            pytest.raises(YazioOperationBusy),
            yazio_operation_slot("user-3"),
        ):
            pass

    with yazio_operation_slot("user-3"):
        pass
