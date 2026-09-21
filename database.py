"""Database setup and durable Agent Relay models (SQLite and PostgreSQL).

This module is intentionally the only place that knows about backend-specific
connection details: SQLite pragmas and its writer-lock transaction, or the
PostgreSQL engine pool sized for concurrent claims. ``USE_ROW_LOCKS`` tells
:mod:`storage` which locking strategy is active; the models and the rest of
the application are backend-agnostic.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def _database_url() -> str:
    return os.getenv("RELAY_DATABASE_URL") or os.getenv("DATABASE_URL") or "sqlite:///./agent-relay.db"


def positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


DATABASE_URL = _database_url()
LEASE_SECONDS = positive_int("RELAY_LEASE_SECONDS", 60)
MAX_ATTEMPTS = positive_int("RELAY_MAX_ATTEMPTS", 5)
RECOVERY_INTERVAL_SECONDS = max(1, positive_int("RELAY_RECOVERY_INTERVAL_SECONDS", 5))
MAX_BODY_BYTES = positive_int("RELAY_MAX_BODY_BYTES", 256 * 1024)
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_db_time(value: datetime) -> datetime:
    """SQLite's DateTime implementation is most portable with naive UTC."""

    return value.astimezone(timezone.utc).replace(tzinfo=None)


def db_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def iso_time(value: datetime | None) -> str | None:
    value = db_time(value)
    if value is None:
        return None
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


class Base(DeclarativeBase):
    pass


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sent_tasks: Mapped[list[Task]] = relationship(
        "Task", foreign_keys="Task.sender_id", back_populates="sender", passive_deletes=True
    )
    received_tasks: Mapped[list[Task]] = relationship(
        "Task", foreign_keys="Task.recipient_id", back_populates="recipient", passive_deletes=True
    )


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("sender_id", "idempotency_key", name="uq_task_sender_idempotency"),)

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    sender_id: Mapped[str] = mapped_column(String(100), ForeignKey("agents.id"), nullable=False, index=True)
    recipient_id: Mapped[str] = mapped_column(String(100), ForeignKey("agents.id"), nullable=False, index=True)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    sender: Mapped[Agent] = relationship("Agent", foreign_keys=[sender_id], back_populates="sent_tasks")
    recipient: Mapped[Agent] = relationship("Agent", foreign_keys=[recipient_id], back_populates="received_tasks")
    attempts: Mapped[list[Attempt]] = relationship(
        "Attempt", back_populates="task", cascade="all, delete-orphan", order_by="Attempt.attempt_number"
    )


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("task_id", "attempt_number", name="uq_attempt_task_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    claim_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    terminal_action: Mapped[str | None] = mapped_column(String(10), nullable=True)
    terminal_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    task: Mapped[Task] = relationship("Task", back_populates="attempts")


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


# SQLite has no ``SELECT ... FOR UPDATE SKIP LOCKED``, so every writer is
# serialized through one ``BEGIN IMMEDIATE`` transaction (see
# ``immediate_transaction`` below). PostgreSQL supports real row locking, so
# there the same call sites take row-level locks instead -- storage.py reads
# this flag to decide whether to add ``.with_for_update(...)`` to a query.
USE_ROW_LOCKS = _is_postgres(DATABASE_URL)

engine_kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
if _is_sqlite(DATABASE_URL):
    engine_kwargs.update({"connect_args": {"check_same_thread": False, "timeout": 30}})
    if DATABASE_URL in {"sqlite://", "sqlite:///:memory:"}:
        from sqlalchemy.pool import StaticPool

        engine_kwargs["poolclass"] = StaticPool
elif _is_postgres(DATABASE_URL):
    # The concurrent-claim test alone opens 16 simultaneous connections (one
    # per worker thread); default pool_size=5/max_overflow=10 would starve it.
    engine_kwargs.update({"pool_size": 20, "max_overflow": 20})

engine: Engine = create_engine(DATABASE_URL, **engine_kwargs)

if _is_sqlite(DATABASE_URL):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False, autoflush=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def db_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def immediate_transaction() -> Generator[Session, None, None]:
    """One writer transaction boundary for claim/heartbeat/terminal/recovery.

    SQLite does not support PostgreSQL's ``FOR UPDATE SKIP LOCKED``.  A
    ``BEGIN IMMEDIATE`` writer reservation serializes every writer (claims,
    recovery, terminal submissions, even the ``last_seen_at`` stamp in
    ``authenticate``) across API processes -- coarse, but it is the only tool
    SQLite offers.

    PostgreSQL supports real row-level locking, so here this is a plain
    transaction; the actual concurrency safety comes from
    ``.with_for_update(...)`` at each call site in storage.py (guarded by
    ``USE_ROW_LOCKS``), which lets unrelated tasks proceed concurrently
    instead of serializing every write in the database.
    """

    if _is_sqlite(DATABASE_URL):
        connection = engine.connect()
        session = Session(bind=connection, expire_on_commit=False, autoflush=True)
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            yield session
            session.flush()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            session.close()
            connection.close()
        return

    session = SessionLocal()
    try:
        yield session
        session.flush()
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def recover_expired_in_session(db: Session, now: datetime) -> int:
    """Expire active leases and requeue/fail their tasks within ``db``.

    Two passes, deliberately: a lock-free scan finds candidate tasks, then
    each is re-checked under its row lock before being mutated. A single
    locked scan (``SELECT ... FOR UPDATE`` over the whole expired set) would
    lock Attempt rows before Task rows; heartbeat/commit_terminal lock Task
    then Attempt (see ``_get_task_for_update`` in storage.py). Two writers
    taking the same pair of locks in opposite order is exactly how Postgres
    deadlocks -- e.g. a heartbeat racing a recovery pass at the lease
    boundary, which is SPEC.md scenario 4/6. Locking Task-then-Attempt here
    too keeps the lock order consistent everywhere, so that race resolves by
    ordinary waiting instead of a deadlock abort.
    """

    now_db = as_db_time(now)
    candidate_task_ids = list(
        db.scalars(
            select(Attempt.task_id)
            .where(Attempt.outcome == "processing", Attempt.lease_expires_at <= now_db)
            .distinct()
        )
    )
    count = 0
    for task_id in candidate_task_ids:
        task_query = select(Task).where(Task.id == task_id)
        if USE_ROW_LOCKS:
            # skip_locked: a task currently locked by a live claim/heartbeat/
            # terminal call isn't stale right now by definition -- skip it
            # this pass rather than wait; RECOVERY_INTERVAL_SECONDS retries.
            task_query = task_query.with_for_update(skip_locked=True)
        task = db.scalar(task_query)
        if task is None:
            continue
        attempt_query = select(Attempt).where(
            Attempt.task_id == task_id,
            Attempt.outcome == "processing",
            Attempt.lease_expires_at <= now_db,
        )
        if USE_ROW_LOCKS:
            attempt_query = attempt_query.with_for_update()
        attempt = db.scalar(attempt_query)
        if attempt is None:
            continue  # renewed by a heartbeat between the scan above and this lock
        attempt.outcome = "expired"
        attempt.finished_at = now_db
        if task.status == "processing":
            if task.attempt_count >= MAX_ATTEMPTS:
                task.status = "failed"
                task.error = "attempts_exhausted"
                task.output = None
                task.finished_at = now_db
            else:
                task.status = "queued"
                task.finished_at = None
        count += 1
    return count


def recover_expired() -> int:
    """Run one recovery pass and return the number of expired attempts."""

    with immediate_transaction() as db:
        return recover_expired_in_session(db, utcnow())


__all__ = [
    "Agent",
    "Attempt",
    "Base",
    "DATABASE_URL",
    "DEFAULT_PAGE_SIZE",
    "LEASE_SECONDS",
    "MAX_ATTEMPTS",
    "MAX_BODY_BYTES",
    "MAX_PAGE_SIZE",
    "RECOVERY_INTERVAL_SECONDS",
    "Task",
    "USE_ROW_LOCKS",
    "as_db_time",
    "db_session",
    "db_time",
    "engine",
    "immediate_transaction",
    "init_db",
    "iso_time",
    "recover_expired",
    "recover_expired_in_session",
    "utcnow",
]
