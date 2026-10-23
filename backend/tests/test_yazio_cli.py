from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app import cli
from app.config import settings
from app.models import HealthSample, ImportBatch, User, YazioConnection
from app.nutrition.models import (
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionIngestionRun,
    NutritionSourceObservation,
)
from app.services import yazio_sync
from app.services.credential_crypto import encrypt_credential
from app.services.yazio_provider import (
    YazioDailyNutrientSummary,
    YazioFoodDiary,
    YazioNutrientValues,
    YazioProviderUnavailableError,
)


@pytest.fixture(autouse=True)
def configured_sdk(monkeypatch):
    monkeypatch.setattr(settings, "yazio_enabled", True)
    monkeypatch.setattr(settings, "yazio_sdk_client_secret", "synthetic-sdk-secret")

    def forbidden_prompt(*_args, **_kwargs):
        pytest.fail("sync-yazio must not prompt for identity or credentials")

    monkeypatch.setattr("builtins.input", forbidden_prompt)
    monkeypatch.setattr(cli.getpass, "getpass", forbidden_prompt)


@pytest.fixture
def connection(db, user):
    connection = YazioConnection(
        user_id=user.id,
        source_identifier="configured-opaque-yazio-source",
        encrypted_email=encrypt_credential("configured@example.test"),
        encrypted_password=encrypt_credential("synthetic-password"),
        sync_enabled=True,
        sync_days=7,
    )
    db.add(connection)
    db.commit()
    return connection


def _synthetic_range_transport(monkeypatch, capsys, expected_start, expected_end):
    def fetch(email, password, start_day, end_day):
        assert (email, password) == ("configured@example.test", "synthetic-password")
        assert (start_day, end_day) == (expected_start, expected_end)
        assert capsys.readouterr().out == ""
        return {"days": {end_day.isoformat(): {"energy": 1800}}}, YazioFoodDiary(
            requested_start_day=start_day,
            requested_end_day=end_day,
            consumed_products=(),
            consumed_simple_products=(),
            product_profiles=(),
            daily_summaries=(
                YazioDailyNutrientSummary(
                    local_date=end_day,
                    nutrients=YazioNutrientValues(energy=Decimal("1800")),
                    energy_goal=None,
                ),
            ),
        )

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fetch)


def test_sync_cli_uses_configured_identity_and_canonical_operation(
    db, user, connection, monkeypatch, capsys,
):
    identity = (
        connection.id, connection.source_identifier,
        connection.encrypted_email, connection.encrypted_password,
    )
    _synthetic_range_transport(
        monkeypatch, capsys, date(2026, 9, 1), date(2026, 9, 2),
    )
    events = []

    def record_event(event, **_kwargs):
        events.append(event)

    monkeypatch.setattr(cli, "log_security_event", record_event)
    monkeypatch.setattr(yazio_sync, "log_security_event", record_event)
    args = cli.parser().parse_args([
        "sync-yazio", "--username", "admin",
        "--from-date", "2026-09-01", "--end-date", "2026-09-02",
    ])
    args.handler(args)

    assert capsys.readouterr().out == (
        "YAZIO-Sync 2026-09-01 bis 2026-09-02: "
        "1 neu, 0 aktualisiert, 0 unverändert, 0 fehlerhaft.\n"
    )
    db.expire_all()
    stored = db.get(YazioConnection, identity[0])
    assert (
        stored.id, stored.source_identifier,
        stored.encrypted_email, stored.encrypted_password,
    ) == identity
    assert db.scalar(select(func.count()).select_from(YazioConnection)) == 1
    assert stored.last_attempt_at is not None
    assert stored.last_success_at is not None
    assert stored.last_error is None
    assert stored.next_sync_at is not None
    sample = db.scalars(select(HealthSample)).one()
    assert sample.user_id == user.id
    assert sample.source_identifier == "configured-opaque-yazio-source"
    assert sample.metric_type == "dietary_energy_kcal"
    assert sample.value == Decimal("1800")
    batch = db.scalars(select(ImportBatch)).one()
    assert batch.user_id == user.id
    assert batch.connector_variant == "sdk-v22"
    assert batch.client_identifier == "yazio-sdk"
    run = db.scalars(select(NutritionIngestionRun)).one()
    assert run.user_id == user.id
    assert run.source_instance_id == identity[0]
    assert (run.requested_start_date, run.requested_end_date) == (
        date(2026, 9, 1), date(2026, 9, 2),
    )
    assert run.projection_pending is False
    observation = db.scalars(select(NutritionSourceObservation)).one()
    assert observation.source_instance_id == identity[0]
    assert observation.ingestion_run_id == run.id
    projection = db.scalars(select(NutritionDailyProjection)).one()
    assert projection.user_id == user.id
    assert projection.local_date == date(2026, 9, 2)
    assert projection.projection_version == 1
    assert db.scalar(
        select(NutritionDailyProjectionFact.value).where(
            NutritionDailyProjectionFact.projection_id == projection.id,
            NutritionDailyProjectionFact.metric_key == "dietary_energy_kcal",
        )
    ) == Decimal("1800")
    assert events.count("integration.yazio.sync_completed") == 1


def test_sync_cli_requires_configured_connection_without_prompt_or_creation(
    db, user, capsys,
):
    args = cli.parser().parse_args(["sync-yazio"])
    with pytest.raises(SystemExit, match="configure-yazio"):
        args.handler(args)
    assert capsys.readouterr().out == ""
    for model in (YazioConnection, ImportBatch, NutritionIngestionRun, HealthSample):
        assert db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("option", ("--email", "--source-identifier"))
def test_sync_cli_rejects_transient_identity_options(option):
    with pytest.raises(SystemExit) as caught:
        cli.parser().parse_args(["sync-yazio", option, "transient@example.test"])
    assert caught.value.code == 2


def test_configure_cli_still_accepts_email():
    args = cli.parser().parse_args([
        "configure-yazio", "--email", "configured@example.test",
    ])
    assert args.email == "configured@example.test"
    assert args.handler is cli.configure_yazio


@pytest.mark.parametrize(("options", "start_day", "end_day"), (
    ([], date(2026, 7, 5), date(2026, 9, 2)),
    (["--days", "3"], date(2026, 8, 31), date(2026, 9, 2)),
    (["--days", "2", "--end-date", "2026-08-02"],
     date(2026, 8, 1), date(2026, 8, 2)),
    (["--from-date", "2026-09-01", "--end-date", "2026-09-02"],
     date(2026, 9, 1), date(2026, 9, 2)),
    (["--from-date", "2025-09-02", "--end-date", "2026-09-02"],
     date(2025, 9, 2), date(2026, 9, 2)),
    (["--days", "366"], date(2025, 9, 2), date(2026, 9, 2)),
))
def test_sync_cli_preserves_days_and_date_validation(
    db, user, connection, monkeypatch, capsys, options, start_day, end_day,
):
    class Today(date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 2)

    monkeypatch.setattr(cli, "date", Today)
    # Named-user selection must not silently fall back to the default admin.
    user.username = "configured-cli-owner"
    db.commit()
    _synthetic_range_transport(monkeypatch, capsys, start_day, end_day)
    assert cli.parser().parse_args(["sync-yazio"]).days == 60
    args = cli.parser().parse_args([
        "sync-yazio", "--username", "configured-cli-owner", *options,
    ])
    args.handler(args)
    run = db.scalars(select(NutritionIngestionRun)).one()
    assert (run.requested_start_date, run.requested_end_date) == (start_day, end_day)
    assert run.source_instance_id == connection.id
    assert run.projection_pending is False
    assert capsys.readouterr().out == (
        f"YAZIO-Sync {start_day.isoformat()} bis {end_day.isoformat()}: "
        "1 neu, 0 aktualisiert, 0 unverändert, 0 fehlerhaft.\n"
    )


@pytest.mark.parametrize(("options", "message"), (
    (["--from-date", "not-a-date"], "YYYY-MM-DD"),
    (["--end-date", "2026-99-99"], "YYYY-MM-DD"),
    (["--from-date", "2026-09-03", "--end-date", "2026-09-02"],
     "--from-date darf nicht nach --end-date liegen"),
    (["--from-date", "2025-09-01", "--end-date", "2026-09-02"], "366 Tage"),
    (["--days", "0"], "--days muss zwischen 1 und 366 liegen"),
    (["--days", "367"], "--days muss zwischen 1 und 366 liegen"),
))
def test_sync_cli_rejects_invalid_ranges_without_import(
    db, user, options, message, capsys,
):
    args = cli.parser().parse_args(["sync-yazio", *options])
    with pytest.raises(SystemExit, match=message):
        args.handler(args)
    assert capsys.readouterr().out == ""
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


def test_sync_cli_prints_no_success_when_canonical_sync_fails(
    db, user, connection, monkeypatch, capsys,
):
    def fail_fetch(*_args, **_kwargs):
        assert capsys.readouterr().out == ""
        raise YazioProviderUnavailableError()

    monkeypatch.setattr(yazio_sync, "fetch_yazio_domain_transport", fail_fetch)
    args = cli.parser().parse_args(["sync-yazio"])
    with pytest.raises(SystemExit, match="vorübergehend nicht erreichbar"):
        args.handler(args)
    assert capsys.readouterr().out == ""
    db.refresh(connection)
    assert connection.last_success_at is None
    assert connection.last_error is not None
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


def test_sync_cli_reports_unknown_user_without_prompt_or_import(db, capsys):
    args = cli.parser().parse_args(["sync-yazio", "--username", "missing"])
    with pytest.raises(SystemExit, match="CaloGraph-Benutzer nicht gefunden"):
        args.handler(args)
    assert capsys.readouterr().out == ""
    assert db.scalar(select(func.count()).select_from(User)) == 0
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
