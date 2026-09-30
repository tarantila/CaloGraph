"""Behavioral regressions for CLI paths touched by Analysis demo integration."""

import argparse
from contextlib import nullcontext
from datetime import UTC, date, datetime

from sqlalchemy import select

import app.cli as cli
from app.models import AccountRecoveryToken, ImportBatch
from app.services.analysis_demo import demo_user_id


def test_seed_cli_anchors_window_to_berlin_local_day(db, user, monkeypatch) -> None:
    class FrozenDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 29)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 9, 29, 23, 30, tzinfo=UTC)
            return instant if tz is None else instant.astimezone(tz)

    monkeypatch.setattr(cli, "date", FrozenDate)
    monkeypatch.setattr(cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(cli, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr("app.services.analysis_demo.getpass.getpass", lambda *_: "synthetic-test-password")

    cli.seed_analysis_demo_command(argparse.Namespace())

    batch = db.scalar(select(ImportBatch).where(ImportBatch.user_id == demo_user_id()))
    assert batch is not None
    assert batch.client_identifier == "analysis-demo:v2:2026-09-30"


def test_one_shot_scheduler_completes_security_cleanup_cycle(db, user, monkeypatch, capsys) -> None:

    expired = AccountRecoveryToken(
        user_id=user.id,
        token_hash="e" * 64,
        created_at=datetime(2000, 1, 1, tzinfo=UTC),
        expires_at=datetime(2000, 1, 2, tzinfo=UTC),
    )
    db.add(expired)
    db.commit()
    expired_id = expired.id
    monkeypatch.setattr(type(cli.settings), "validate_runtime_security", lambda _self, _purpose: None)
    monkeypatch.setattr(cli, "warn_legacy_yazio_provider", lambda _logger: None)
    monkeypatch.setattr(cli, "_touch_yazio_scheduler_heartbeat", lambda: None)
    monkeypatch.setattr(cli, "run_due_yazio_syncs", lambda **_kwargs: (0, 0))
    monkeypatch.setattr(cli.time, "monotonic", lambda: 3601.0)
    monkeypatch.setattr(cli, "SessionLocal", lambda: nullcontext(db))

    cli.run_yazio_scheduler(argparse.Namespace(once=True))

    assert capsys.readouterr().out == "0 fällige Verbindung(en), 0 erfolgreich.\n"
    assert db.get(AccountRecoveryToken, expired_id) is None
