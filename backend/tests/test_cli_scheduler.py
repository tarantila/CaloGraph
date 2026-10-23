"""Behavioral regressions for CLI paths touched by Analysis demo integration."""

import argparse
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta

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


def test_seed_cli_opts_into_one_current_day_supplement(db, user, monkeypatch) -> None:
    from app.nutrition.models import NutritionIngestionRun
    from app.services.analysis_demo import seed_analysis_demo

    anchor = date(2026, 9, 29)
    demo_id = seed_analysis_demo(
        db, password="synthetic-cli-test-password", today=anchor,
    )

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 9, 29, 23, 30, tzinfo=UTC)
            return instant if tz is None else instant.astimezone(tz)

    monkeypatch.setattr(cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(cli, "SessionLocal", lambda: nullcontext(db))

    args = cli.parser().parse_args([
        "seed-analysis-demo", "--include-current-day-supplement",
    ])
    cli.seed_analysis_demo_command(args)

    batch = db.scalar(select(ImportBatch).where(ImportBatch.user_id == demo_id))
    assert batch is not None
    assert batch.client_identifier == "analysis-demo:v2:2026-09-29"
    runs = list(db.scalars(
        select(NutritionIngestionRun).where(NutritionIngestionRun.user_id == demo_id)
    ))
    assert len(runs) == 2
    supplement = next(run for run in runs if run.requested_start_date == date(2026, 9, 30))
    assert supplement.requested_end_date == date(2026, 9, 30)
    assert supplement.status == "completed"
    assert supplement.provider_metadata == {
        "base_seed": f"{(anchor - timedelta(days=89)).isoformat()}:{anchor.isoformat()}",
        "supplement": "current-day-v1",
        "target_date": "2026-09-30",
    }


def test_seed_cli_adds_follow_up_meals_and_their_projection_days(db, user, monkeypatch) -> None:
    from app.analytics.nutrition_analysis import read_analysis_nutrition
    from app.analytics.provider_selection import NutritionProviderSelection
    from app.models import User
    from app.nutrition.models import NutritionIngestionRun
    from app.services.analysis_demo import demo_source_instance_id, seed_analysis_demo

    anchor = date(2026, 9, 29)
    demo_id = seed_analysis_demo(
        db, password="synthetic-cli-test-password", today=anchor,
    )

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 9, 29, 23, 30, tzinfo=UTC)
            return instant if tz is None else instant.astimezone(tz)

    monkeypatch.setattr(cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(cli, "SessionLocal", lambda: nullcontext(db))

    args = cli.parser().parse_args(["seed-analysis-demo", "--include-follow-up-meals"])
    cli.seed_analysis_demo_command(args)

    runs = list(db.scalars(
        select(NutritionIngestionRun).where(NutritionIngestionRun.user_id == demo_id)
    ))
    assert {run.connector_variant for run in runs} == {
        "analysis-demo-v2",
        "analysis-demo-current-day-v1",
        "analysis-demo-follow-up-v1",
    }
    account = db.get(User, demo_id)
    assert account is not None
    source = demo_source_instance_id(demo_id)
    selection = NutritionProviderSelection("analysis_demo", source, (("analysis_demo", source),))
    today = date(2026, 9, 30)
    tomorrow = today + timedelta(days=1)
    assert [meal.display_name for meal in read_analysis_nutrition(
        db, user_id=demo_id, selection=selection, start=today, end=today,
    ).days[0].meals] == [
        "Haferflocken mit Beeren", "Linsensalat", "Lachs mit Kartoffeln", "Vanillepudding",
    ]
    assert [meal.display_name for meal in read_analysis_nutrition(
        db, user_id=demo_id, selection=selection, start=tomorrow, end=tomorrow,
    ).days[0].meals] == [
        "Vollkornbrot mit Ei", "Kichererbsen-Bowl", "Gemüsepfanne mit Reis",
    ]


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
    monkeypatch.setattr(cli, "_touch_yazio_scheduler_heartbeat", lambda: None)
    monkeypatch.setattr(cli, "run_due_yazio_syncs", lambda **_kwargs: (0, 0))
    monkeypatch.setattr(cli.time, "monotonic", lambda: 3601.0)
    monkeypatch.setattr(cli, "SessionLocal", lambda: nullcontext(db))

    cli.run_yazio_scheduler(argparse.Namespace(once=True))

    assert capsys.readouterr().out == "0 fällige Verbindung(en), 0 erfolgreich.\n"
    assert db.get(AccountRecoveryToken, expired_id) is None
