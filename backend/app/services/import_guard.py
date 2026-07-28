import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.database import engine


class ImportAlreadyRunning(RuntimeError):
    pass


_local_locks: dict[str, threading.Lock] = {}
_local_locks_guard = threading.Lock()


def _lock_id(user_id: object) -> int:
    digest = hashlib.sha256(f"calograph-import:{user_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


@contextmanager
def import_slot(user_id: object) -> Iterator[None]:
    if engine.dialect.name == "postgresql":
        lock_id = _lock_id(user_id)
        with engine.connect() as connection:
            acquired = bool(
                connection.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id},
                )
            )
            if not acquired:
                raise ImportAlreadyRunning
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": lock_id},
                )
        return

    key = str(user_id)
    with _local_locks_guard:
        lock = _local_locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ImportAlreadyRunning
    try:
        yield
    finally:
        lock.release()
