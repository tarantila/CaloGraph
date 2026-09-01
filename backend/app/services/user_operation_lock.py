import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from app.models import User


class UserOperationBusy(RuntimeError):
    pass


class InactiveUserOperation(RuntimeError):
    pass


class _LocalOperationLock:
    def __init__(self) -> None:
        self._guard = threading.Condition()
        self._shared_count = 0
        self._exclusive = False

    def acquire(self, *, shared: bool, wait: bool = False) -> bool:
        with self._guard:
            while self._exclusive or (not shared and self._shared_count):
                if not wait:
                    return False
                self._guard.wait()
            if shared:
                self._shared_count += 1
            else:
                self._exclusive = True
            return True

    def release(self, *, shared: bool) -> None:
        with self._guard:
            if shared:
                if self._shared_count <= 0:
                    raise RuntimeError("shared local user-operation lock is not held")
                self._shared_count -= 1
            else:
                if not self._exclusive:
                    raise RuntimeError("exclusive local user-operation lock is not held")
                self._exclusive = False
            self._guard.notify_all()


_LOCAL_LOCKS: dict[int, _LocalOperationLock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()
_ADMIN_INVARIANT_LOCK_ID = 0


def advisory_lock_id(namespace: str, value: object) -> int:
    material = f"calograph:{namespace}:{value}".encode()
    digest = hashlib.sha256(material).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def user_operation_lock_id(user_id: UUID) -> int:
    return advisory_lock_id("user-operation", user_id)


def _admin_invariant_lock_id() -> int:
    global _ADMIN_INVARIANT_LOCK_ID
    if _ADMIN_INVARIANT_LOCK_ID == 0:
        _ADMIN_INVARIANT_LOCK_ID = advisory_lock_id("admin-invariant", "active-admin")
    return _ADMIN_INVARIANT_LOCK_ID


def _engine_for(db: Session) -> Engine:
    bind = db.get_bind()
    if isinstance(bind, Connection):
        return bind.engine
    return bind


@contextmanager
def _local_lock(lock_id: int, *, shared: bool, wait: bool = False) -> Iterator[None]:
    with _LOCAL_LOCKS_GUARD:
        lock = _LOCAL_LOCKS.setdefault(lock_id, _LocalOperationLock())
    if not lock.acquire(shared=shared, wait=wait):
        raise UserOperationBusy
    try:
        yield
    finally:
        lock.release(shared=shared)


@contextmanager
def _postgres_lock(
    engine: Engine, lock_id: int, *, shared: bool, wait: bool = False
) -> Iterator[None]:
    if wait:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": lock_id},
                )
        return
    acquire_function = "pg_try_advisory_lock_shared" if shared else "pg_try_advisory_lock"
    release_function = "pg_advisory_unlock_shared" if shared else "pg_advisory_unlock"
    with engine.connect() as connection:
        acquired = bool(
            connection.scalar(
                text(f"SELECT {acquire_function}(:lock_id)"),
                {"lock_id": lock_id},
            )
        )
        if not acquired:
            raise UserOperationBusy
        try:
            yield
        finally:
            connection.execute(
                text(f"SELECT {release_function}(:lock_id)"),
                {"lock_id": lock_id},
            )

@contextmanager
def _operation_lock(db: Session, lock_id: int, *, shared: bool) -> Iterator[None]:
    engine = _engine_for(db)
    if engine.dialect.name == "postgresql":
        with _postgres_lock(engine, lock_id, shared=shared):
            yield
        return
    with _local_lock(lock_id, shared=shared):
        yield


def _fresh_user(db: Session, user_id: UUID) -> User | None:
    return db.scalar(
        select(User)
        .where(User.id == user_id)
        .execution_options(populate_existing=True)
    )


@contextmanager
def shared_user_operation(db: Session, user_id: UUID) -> Iterator[User]:
    with _operation_lock(db, user_operation_lock_id(user_id), shared=True):
        user = _fresh_user(db, user_id)
        if user is None or not user.is_active:
            raise InactiveUserOperation("CaloGraph-Benutzer ist nicht aktiv.")
        yield user


@contextmanager
def exclusive_user_lifecycle_operation(db: Session, user_id: UUID) -> Iterator[None]:
    with _operation_lock(db, user_operation_lock_id(user_id), shared=False):
        yield


@contextmanager
def exclusive_admin_invariant_operation(db: Session) -> Iterator[None]:
    with _operation_lock(db, _admin_invariant_lock_id(), shared=False):
        yield


@contextmanager
def exclusive_initial_user_operation(db: Session) -> Iterator[None]:
    """Serialize every first-user creator across API workers and the CLI."""
    lock_id = advisory_lock_id("initial-user", "singleton")
    engine = _engine_for(db)
    if engine.dialect.name == "postgresql":
        with _postgres_lock(engine, lock_id, shared=False, wait=True):
            yield
        return
    with _local_lock(lock_id, shared=False, wait=True):
        if engine.dialect.name == "sqlite":
            # BEGIN IMMEDIATE serializes writers for file-backed SQLite too.
            db.execute(text("BEGIN IMMEDIATE"))
        yield
