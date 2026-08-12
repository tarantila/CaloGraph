import hashlib
import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

from alembic import command
from app.config import settings
from app.database import engine as application_engine
from app.services.credential_crypto import encrypt_credential
from app.services.yazio_sync import run_scheduled_yazio_sync

POSTGRES_TESTS_ENABLED = (
    os.environ.get("CALOGRAPH_ALLOW_DESTRUCTIVE_POSTGRES_TESTS") == "1"
    and bool(os.environ.get("CALOGRAPH_POSTGRES_TEST_URL"))
)
PREVIOUS_REVISION = "20260729_0008"
TARGET_REVISION = "20260803_0009"


@pytest.mark.skipif(
    not POSTGRES_TESTS_ENABLED,
    reason="isolated PostgreSQL integration tests are not explicitly enabled",
)
def test_legacy_yazio_identifiers_are_migrated_without_duplicates(monkeypatch) -> None:
    assert application_engine.dialect.name == "postgresql"
    database_url = os.environ["CALOGRAPH_POSTGRES_TEST_URL"]
    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(alembic_config, PREVIOUS_REVISION)

    engine = create_engine(database_url, pool_pre_ping=True)
    user_id = uuid.UUID("10000000-0000-0000-0000-000000000001")
    secondary_user_id = uuid.UUID("10000000-0000-0000-0000-000000000002")
    connection_id = uuid.UUID("20000000-0000-0000-0000-000000000001")
    range_connection_id = uuid.UUID("20000000-0000-0000-0000-000000000002")
    batch_id = uuid.UUID("30000000-0000-0000-0000-000000000001")
    secondary_batch_id = uuid.UUID("30000000-0000-0000-0000-000000000002")
    active_source = "yazio:0123456789abcdef"
    historical_sources = (
        "yazio:1111111111111111",
        "yazio:2222222222222222",
    )
    sample_rows = [
        {
            "id": uuid.UUID("40000000-0000-0000-0000-000000000001"),
            "user_id": user_id,
            "import_batch_id": batch_id,
            "external_sample_id": "2026-07-23:dietary_energy_kcal",
            "fingerprint": "a" * 64,
            "source_identifier": active_source,
            "metric_type": "dietary_energy_kcal",
            "value": Decimal("1800"),
            "unit": "kcal",
            "local_date": date(2026, 7, 23),
            "start_at": datetime(2026, 7, 23, 10, tzinfo=UTC),
        },
        {
            "id": uuid.UUID("40000000-0000-0000-0000-000000000002"),
            "user_id": user_id,
            "import_batch_id": batch_id,
            "external_sample_id": "2026-07-23:protein_g",
            "fingerprint": "b" * 64,
            "source_identifier": active_source,
            "metric_type": "protein_g",
            "value": Decimal("120"),
            "unit": "g",
            "local_date": date(2026, 7, 23),
            "start_at": datetime(2026, 7, 23, 10, tzinfo=UTC),
        },
        {
            "id": uuid.UUID("40000000-0000-0000-0000-000000000003"),
            "user_id": user_id,
            "import_batch_id": batch_id,
            "external_sample_id": "2026-07-22:dietary_energy_kcal",
            "fingerprint": "c" * 64,
            "source_identifier": historical_sources[0],
            "metric_type": "dietary_energy_kcal",
            "value": Decimal("1700"),
            "unit": "kcal",
            "local_date": date(2026, 7, 22),
            "start_at": datetime(2026, 7, 22, 10, tzinfo=UTC),
        },
        {
            "id": uuid.UUID("40000000-0000-0000-0000-000000000004"),
            "user_id": user_id,
            "import_batch_id": batch_id,
            "external_sample_id": "2026-07-21:dietary_energy_kcal",
            "fingerprint": "d" * 64,
            "source_identifier": historical_sources[1],
            "metric_type": "dietary_energy_kcal",
            "value": Decimal("1600"),
            "unit": "kcal",
            "local_date": date(2026, 7, 21),
            "start_at": datetime(2026, 7, 21, 10, tzinfo=UTC),
        },
        {
            "id": uuid.UUID("40000000-0000-0000-0000-000000000005"),
            "user_id": user_id,
            "import_batch_id": batch_id,
            "external_sample_id": "2026-07-20:dietary_energy_kcal",
            "fingerprint": "e" * 64,
            "source_identifier": historical_sources[0],
            "metric_type": "dietary_energy_kcal",
            "value": Decimal("1500"),
            "unit": "kcal",
            "local_date": date(2026, 7, 20),
            "start_at": datetime(2026, 7, 20, 10, tzinfo=UTC),
        },
        {
            "id": uuid.UUID("40000000-0000-0000-0000-000000000006"),
            "user_id": secondary_user_id,
            "import_batch_id": secondary_batch_id,
            "external_sample_id": "2026-07-19:dietary_energy_kcal",
            "fingerprint": "f" * 64,
            "source_identifier": historical_sources[0],
            "metric_type": "dietary_energy_kcal",
            "value": Decimal("1400"),
            "unit": "kcal",
            "local_date": date(2026, 7, 19),
            "start_at": datetime(2026, 7, 19, 10, tzinfo=UTC),
        },
    ]
    expected_migration_fingerprints = {
        row["id"]: (
            hashlib.md5(
                f"{row['id']}-opaque-yazio-source-v1".encode(), usedforsecurity=False
            ).hexdigest()
            + hashlib.md5(
                f"v1-{row['id']}".encode(), usedforsecurity=False
            ).hexdigest()
        )
        for row in sample_rows
    }

    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    encrypted_email = encrypt_credential("legacy-owner@example.com")
    encrypted_password = encrypt_credential("legacy-yazio-password")
    now = datetime(2026, 7, 23, 10, tzinfo=UTC)

    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == PREVIOUS_REVISION
            connection.execute(
                text(
                    """
                    INSERT INTO users (
                        id, username, password_hash, language, timezone,
                        week_starts_on, preferred_weight_unit,
                        raw_payload_retention_days, is_active, is_admin,
                        created_at, updated_at
                    ) VALUES (
                        :id, :username, :password_hash, 'de', 'Europe/Berlin',
                        0, 'kg', 0, true, false, :created_at, :updated_at
                    )
                    """
                ),
                [
                    {
                        "id": user_id,
                        "username": "legacy-yazio-user",
                        "password_hash": "legacy-password-hash",
                        "created_at": now,
                        "updated_at": now,
                    },
                    {
                        "id": secondary_user_id,
                        "username": "secondary-legacy-yazio-user",
                        "password_hash": "legacy-password-hash",
                        "created_at": now,
                        "updated_at": now,
                    },
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO yazio_connections (
                        id, user_id, encrypted_email, encrypted_password,
                        account_hash, source_identifier, sync_enabled,
                        sync_interval_minutes, sync_days, next_sync_at,
                        created_at, updated_at
                    ) VALUES (
                        :id, :user_id, :encrypted_email, :encrypted_password,
                        :account_hash, :source_identifier, true,
                        360, 7, :next_sync_at, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": connection_id,
                    "user_id": user_id,
                    "encrypted_email": encrypted_email,
                    "encrypted_password": encrypted_password,
                    "account_hash": "f" * 64,
                    "source_identifier": active_source,
                    "next_sync_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO import_batches (
                        id, user_id, source_type, client_identifier, status,
                        started_at, finished_at, received, inserted, updated,
                        skipped, failed, unknown_types, payload_hash
                    ) VALUES (
                        :id, :user_id, 'yazio_export_v1', :client_identifier,
                        'completed', :started_at, :finished_at,
                        :received, :received, 0, 0, 0, '[]'::json, :payload_hash
                    )
                    """
                ),
                [
                    {
                        "id": batch_id,
                        "user_id": user_id,
                        "client_identifier": "legacy-migration-test",
                        "received": 5,
                        "started_at": now,
                        "finished_at": now,
                        "payload_hash": "e" * 64,
                    },
                    {
                        "id": secondary_batch_id,
                        "user_id": secondary_user_id,
                        "client_identifier": "secondary-legacy-migration-test",
                        "received": 1,
                        "started_at": now,
                        "finished_at": now,
                        "payload_hash": "f" * 64,
                    },
                ],
            )
            connection.execute(
                text(
                    """
                    INSERT INTO health_samples (
                        id, user_id, import_batch_id, external_sample_id,
                        fingerprint, source_type, source_name, source_identifier,
                        metric_type, value, unit, original_value, original_unit,
                        start_at, end_at, local_date, timezone, created_at, updated_at
                    ) VALUES (
                        :id, :user_id, :import_batch_id, :external_sample_id,
                        :fingerprint, 'yazio_export_v1', 'YAZIO', :source_identifier,
                        :metric_type, :value, :unit, :value, :unit,
                        :start_at, :start_at, :local_date, 'Europe/Berlin',
                        :created_at, :updated_at
                    )
                    """
                ),
                [
                    {
                        **row,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for row in sample_rows
                ],
            )
            legacy_samples = {
                row["id"]: {
                    key: value
                    for key, value in row.items()
                    if key not in {"source_identifier", "fingerprint"}
                }
                for row in connection.execute(
                    text("SELECT * FROM health_samples ORDER BY id")
                ).mappings()
            }

        command.upgrade(alembic_config, TARGET_REVISION)

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == TARGET_REVISION
            columns = {
                column["name"]
                for column in sa.inspect(connection).get_columns("yazio_connections")
            }
            assert "account_hash" not in columns
            assert connection.scalar(text("SELECT count(*) FROM users")) == 2
            assert connection.scalar(text("SELECT count(*) FROM yazio_connections")) == 1
            assert connection.scalar(text("SELECT count(*) FROM import_batches")) == 2
            migrated_connection_source = connection.scalar(
                text("SELECT source_identifier FROM yazio_connections WHERE id = :id"),
                {"id": connection_id},
            )
            migrated_samples = list(
                connection.execute(
                    text(
                        """
                            SELECT *
                            FROM health_samples
                            ORDER BY id
                        """
                    )
                ).mappings()
            )

        active_identifier = f"yazio:{user_id}"
        assert migrated_connection_source == active_identifier
        assert len(migrated_samples) == len(sample_rows)
        assert {row["id"] for row in migrated_samples} == set(legacy_samples)
        for row in migrated_samples:
            assert {
                key: value
                for key, value in row.items()
                if key not in {"source_identifier", "fingerprint"}
            } == legacy_samples[row["id"]]
            assert row["fingerprint"] == expected_migration_fingerprints[row["id"]]

        active_samples = [
            row for row in migrated_samples if row["id"] in {sample_rows[0]["id"], sample_rows[1]["id"]}
        ]
        assert {row["source_identifier"] for row in active_samples} == {active_identifier}
        migrated_by_id = {row["id"]: row for row in migrated_samples}
        primary_historical_identifier = migrated_by_id[sample_rows[2]["id"]][
            "source_identifier"
        ]
        assert (
            migrated_by_id[sample_rows[4]["id"]]["source_identifier"]
            == primary_historical_identifier
        )
        distinct_historical_identifiers = {
            primary_historical_identifier,
            migrated_by_id[sample_rows[3]["id"]]["source_identifier"],
            migrated_by_id[sample_rows[5]["id"]]["source_identifier"],
        }
        assert len(distinct_historical_identifiers) == 3
        assert all(
            identifier.startswith("yazio:legacy:")
            for identifier in distinct_historical_identifiers
        )
        assert len({row["fingerprint"] for row in migrated_samples}) == len(sample_rows)

        # Runtime models follow Alembic head even though this test isolates the
        # 0009 data rewrite above.
        command.upgrade(alembic_config, "20260812_0013")
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE yazio_connections
                    SET initial_sync_state = 'failed',
                        historical_sync_kind = 'full',
                        historical_sync_state = 'failed',
                        historical_sync_start_date = DATE '2000-01-01',
                        historical_sync_end_date = DATE '2026-07-23',
                        historical_sync_cursor_date = DATE '2026-07-22',
                        historical_sync_started_at = :now,
                        historical_sync_completed_at = :now,
                        historical_sync_last_error = 'legacy failure'
                    WHERE id = :id
                    """
                ),
                {"id": connection_id, "now": now},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO yazio_connections (
                        id, user_id, encrypted_email, encrypted_password,
                        source_identifier, sync_enabled, sync_interval_minutes,
                        sync_days, initial_sync_state, historical_sync_state,
                        next_sync_at, created_at, updated_at
                    ) VALUES (
                        :id, :user_id, :encrypted_email, :encrypted_password,
                        :source_identifier, true, NULL, NULL, 'completed', 'idle',
                        :next_sync_at, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": range_connection_id,
                    "user_id": secondary_user_id,
                    "encrypted_email": encrypted_email,
                    "encrypted_password": encrypted_password,
                    "source_identifier": f"yazio:{secondary_user_id}",
                    "next_sync_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            connection.execute(
                text(
                    """
                    UPDATE yazio_connections
                    SET initial_sync_state = 'completed',
                        historical_sync_kind = 'range',
                        historical_sync_state = 'failed',
                        historical_sync_start_date = DATE '2026-07-01',
                        historical_sync_end_date = DATE '2026-07-23',
                        historical_sync_cursor_date = DATE '2026-07-12',
                        historical_sync_started_at = :now,
                        historical_sync_completed_at = NULL,
                        historical_sync_last_error = 'retryable range failure'
                    WHERE id = :id
                    """
                ),
                {"id": range_connection_id, "now": now},
            )

        command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            columns = {
                column["name"]
                for column in sa.inspect(connection).get_columns("yazio_connections")
            }
            upgraded_connection = connection.execute(
                text(
                    """
                    SELECT sync_interval_minutes, sync_days, historical_sync_state,
                           historical_sync_start_date, historical_sync_end_date,
                           historical_sync_cursor_date, historical_sync_started_at,
                           historical_sync_completed_at, historical_sync_last_error
                    FROM yazio_connections
                    WHERE id = :id
                    """
                ),
                {"id": connection_id},
            ).mappings().one()
            preserved_range_connection = connection.execute(
                text(
                    """
                    SELECT sync_interval_minutes, sync_days, historical_sync_state,
                           historical_sync_start_date, historical_sync_end_date,
                           historical_sync_cursor_date, historical_sync_started_at,
                           historical_sync_completed_at, historical_sync_last_error
                    FROM yazio_connections
                    WHERE id = :id
                    """
                ),
                {"id": range_connection_id},
            ).mappings().one()
        assert "initial_sync_state" not in columns
        assert "historical_sync_kind" not in columns
        assert upgraded_connection == {
            "sync_interval_minutes": None,
            "sync_days": None,
            "historical_sync_state": "idle",
            "historical_sync_start_date": None,
            "historical_sync_end_date": None,
            "historical_sync_cursor_date": None,
            "historical_sync_started_at": None,
            "historical_sync_completed_at": None,
            "historical_sync_last_error": None,
        }
        assert preserved_range_connection == {
            "sync_interval_minutes": None,
            "sync_days": None,
            "historical_sync_state": "failed",
            "historical_sync_start_date": date(2026, 7, 1),
            "historical_sync_end_date": date(2026, 7, 23),
            "historical_sync_cursor_date": date(2026, 7, 12),
            "historical_sync_started_at": now,
            "historical_sync_completed_at": None,
            "historical_sync_last_error": "retryable range failure",
        }


        def fetch_existing_sample(
            email: str,
            password: str,
            _start_day,
            _end_day,
            _include_micronutrients: bool,
        ) -> dict[str, object]:
            assert email == "legacy-owner@example.com"
            assert password == "legacy-yazio-password"
            return {
                "2026-07-23": {
                    "daily_summary": {
                        "meals": {
                            "dinner": {
                                "nutrients": {"energy.energy": 1850},
                            }
                        }
                    }
                }
            }

        summary = run_scheduled_yazio_sync(
            connection_id,
            fetcher=fetch_existing_sample,
            now=datetime(2026, 7, 23, 12, tzinfo=UTC),
        )
        assert summary is not None
        assert summary.inserted == 0
        assert summary.updated == 1

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM health_samples")) == len(sample_rows)
            active_energy_rows = list(
                connection.execute(
                    text(
                        """
                        SELECT id, source_identifier, value
                        FROM health_samples
                        WHERE external_sample_id = :external_sample_id
                        """
                    ),
                    {"external_sample_id": sample_rows[0]["external_sample_id"]},
                ).mappings()
            )
        assert active_energy_rows == [
            {
                "id": sample_rows[0]["id"],
                "source_identifier": active_identifier,
                "value": Decimal("1850.000000"),
            }
        ]
        command.downgrade(alembic_config, "20260812_0013")
        with engine.connect() as connection:
            rolled_back_range_connection = connection.execute(
                text(
                    """
                    SELECT initial_sync_state, historical_sync_kind, historical_sync_state
                    FROM yazio_connections
                    WHERE id = :id
                    """
                ),
                {"id": range_connection_id},
            ).mappings().one()
        assert rolled_back_range_connection == {
            "initial_sync_state": "not_confirmed",
            "historical_sync_kind": "range",
            "historical_sync_state": "failed",
        }
        command.upgrade(alembic_config, "head")
    finally:
        engine.dispose()
