import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.config import settings
from app.database import engine


class YazioOperationBusy(RuntimeError):
    pass


_local_guard = threading.Lock()
_local_users: set[str] = set()
_local_active = 0


def _lock_id(value: str) -> int:
    digest = hashlib.sha256(f"calograph-yazio:{value}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@contextmanager
def yazio_operation_slot(operation_key: object) -> Iterator[None]:
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            user_lock_id = _lock_id(f"user:{operation_key}")
            user_acquired = bool(
                connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": user_lock_id},
                )
            )
            if not user_acquired:
                raise YazioOperationBusy

            slot_lock_id: int | None = None
            try:
                for slot in range(settings.yazio_max_parallel_operations):
                    candidate = _lock_id(f"global-slot:{slot}")
                    if connection.scalar(
                        text("SELECT pg_try_advisory_lock(:lock_id)"),
                        {"lock_id": candidate},
                    ):
                        slot_lock_id = candidate
                        break
                if slot_lock_id is None:
                    raise YazioOperationBusy
                yield
            finally:
                if slot_lock_id is not None:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": slot_lock_id},
                    )
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": user_lock_id},
                )
        return

    key = str(operation_key)
    global _local_active
    with _local_guard:
        if (
            key in _local_users
            or _local_active >= settings.yazio_max_parallel_operations
        ):
            raise YazioOperationBusy
        _local_users.add(key)
        _local_active += 1
    try:
        yield
    finally:
        with _local_guard:
            _local_users.discard(key)
            _local_active -= 1
