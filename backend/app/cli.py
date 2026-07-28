import argparse
import getpass
import logging
import random
import sys
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.auth.security import create_api_token, hash_password
from app.config import ProductionConfigurationError, settings
from app.database import SessionLocal
from app.importers.common import CanonicalSample
from app.importers.json_adapter import AdapterResult
from app.models import NutritionTarget, TrackingQualitySettings, User, YazioConnection
from app.services.credential_crypto import (
    CredentialEncryptionError,
    generate_credential_key,
)
from app.services.import_service import persist_import, purge_expired_raw_payloads
from app.services.yazio_sync import (
    YazioSyncError,
    configure_yazio_connection,
    run_due_yazio_syncs,
    sync_yazio_user,
    validate_yazio_credentials,
)

logger = logging.getLogger("calograph.cli")


def create_user(args: argparse.Namespace) -> None:
    username = args.username or input("Benutzername: ").strip()
    password = args.password or getpass.getpass("Passwort (mindestens 12 Zeichen): ")
    if len(password) < 12:
        raise SystemExit("Das Passwort muss mindestens 12 Zeichen lang sein.")
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == username)):
            if args.if_not_exists:
                print(f"Benutzer '{username}' existiert bereits.")
                return
            raise SystemExit("Benutzer existiert bereits.")
        is_first_user = (db.scalar(select(func.count(User.id))) or 0) == 0
        user = User(
            username=username,
            password_hash=hash_password(password),
            timezone=args.timezone,
            raw_payload_retention_days=args.raw_retention_days,
            is_admin=args.admin or is_first_user,
        )
        db.add(user)
        db.flush()
        db.add(TrackingQualitySettings(user_id=user.id))
        db.add(
            NutritionTarget(
                user_id=user.id,
                valid_from=date.today(),
                calories_kcal=Decimal("2200"),
                protein_g=Decimal("140"),
            )
        )
        db.commit()
    print(f"Benutzer '{username}' wurde angelegt.")


def create_token(args: argparse.Namespace) -> None:
    username = args.username or input("Benutzername: ").strip()
    label = args.label or input("Bezeichnung des Import-Clients: ").strip()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if not user:
            raise SystemExit("Benutzer nicht gefunden.")
        _, raw = create_api_token(db, user, label)
    print("Import-Token (wird nur dieses eine Mal angezeigt):")
    print(raw)


def seed_demo(args: argparse.Namespace) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == args.username))
        if not user:
            raise SystemExit("Benutzer nicht gefunden. Zuerst create-user ausführen.")
        start = date(2026, 2, 15)
        change_day = start + timedelta(days=60)
        existing_target = db.scalar(
            select(NutritionTarget).where(
                NutritionTarget.user_id == user.id, NutritionTarget.valid_from == start
            )
        )
        if not existing_target:
            current = db.scalar(
                select(NutritionTarget)
                .where(NutritionTarget.user_id == user.id, NutritionTarget.valid_to.is_(None))
                .order_by(NutritionTarget.valid_from.desc())
            )
            if current and current.valid_from < start:
                current.valid_to = start
            db.add(
                NutritionTarget(
                    user_id=user.id,
                    valid_from=start,
                    valid_to=change_day,
                    calories_kcal=Decimal("2200"),
                    protein_g=Decimal("140"),
                )
            )
            db.add(
                NutritionTarget(
                    user_id=user.id,
                    valid_from=change_day,
                    calories_kcal=Decimal("2050"),
                    protein_g=Decimal("150"),
                )
            )
            db.flush()
        result = _demo_samples(user.timezone, start, 120)
        summary = persist_import(
            db, user, result, None, "application/x-synthetic", "seed-demo-data"
        )
    print(
        f"Demo-Import abgeschlossen: {summary.inserted} neu, "
        f"{summary.updated} aktualisiert, {summary.skipped} unverändert."
    )


def _demo_samples(timezone: str, start: date, days: int) -> AdapterResult:
    randomizer = random.Random(20260329)
    zone = ZoneInfo(timezone)
    samples: list[CanonicalSample] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        if offset % 23 == 0:
            continue
        target = Decimal("2200") if offset < 60 else Decimal("2050")
        weekend = Decimal("260") if day.weekday() >= 5 else Decimal()
        calories = target + weekend + Decimal(randomizer.randint(-280, 260))
        small_day = offset % 17 == 0
        if small_day:
            calories = Decimal(randomizer.randint(250, 700))
        protein = (calories * Decimal("0.27") / Decimal(4)).quantize(Decimal("0.1"))
        carbs = (calories * Decimal("0.43") / Decimal(4)).quantize(Decimal("0.1"))
        fat = (calories * Decimal("0.30") / Decimal(9)).quantize(Decimal("0.1"))
        at = datetime(day.year, day.month, day.day, 12, tzinfo=zone)
        metrics = [
            ("dietary_energy_kcal", calories, "kcal"),
            ("protein_g", protein, "g"),
            ("carbohydrates_g", carbs, "g"),
            ("fat_g", fat, "g"),
        ]
        if small_day:
            metrics = metrics[:2]
        for metric, value, unit in metrics:
            samples.append(
                CanonicalSample(
                    metric_type=metric,
                    value=value,
                    unit=unit,
                    original_value=value,
                    original_unit=unit,
                    start_at=at,
                    end_at=at,
                    timezone=timezone,
                    source_type="synthetic_demo",
                    source_name="CaloGraph Seed",
                    source_identifier="seed-v1",
                    external_sample_id=f"demo-{day.isoformat()}-{metric}",
                )
            )
    return AdapterResult(source_type="synthetic_demo", samples=samples, received=len(samples))


def purge_raw(_: argparse.Namespace) -> None:
    with SessionLocal() as db:
        count = purge_expired_raw_payloads(db)
    print(f"{count} abgelaufene Rohpayloads gelöscht.")


def sync_yazio(args: argparse.Namespace) -> None:
    username = args.username or input("CaloGraph-Benutzername: ").strip()
    try:
        end_day = date.fromisoformat(args.end_date) if args.end_date else date.today()
        if args.from_date:
            start_day = date.fromisoformat(args.from_date)
        else:
            if not 1 <= args.days <= 366:
                raise SystemExit("--days muss zwischen 1 und 366 liegen.")
            start_day = end_day - timedelta(days=args.days - 1)
    except ValueError as exc:
        raise SystemExit("Datumsangaben müssen das Format YYYY-MM-DD verwenden.") from exc
    if start_day > end_day:
        raise SystemExit("--from-date darf nicht nach --end-date liegen.")
    if (end_day - start_day).days >= 366:
        raise SystemExit("Ein einzelner YAZIO-Abruf ist auf 366 Tage begrenzt.")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if not user:
            raise SystemExit("CaloGraph-Benutzer nicht gefunden.")

    email = args.email or input("YAZIO-E-Mail-Adresse: ").strip()
    password = getpass.getpass("YAZIO-Passwort (wird nicht gespeichert): ")
    if not email or not password:
        raise SystemExit("YAZIO-E-Mail-Adresse und Passwort sind erforderlich.")
    try:
        summary = sync_yazio_user(
            user,
            email,
            password,
            start_day,
            end_day,
            args.source_identifier,
        )
    except YazioSyncError as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"YAZIO-Sync {start_day.isoformat()} bis {end_day.isoformat()}: "
        f"{summary.inserted} neu, {summary.updated} aktualisiert, "
        f"{summary.skipped} unverändert, {summary.failed} fehlerhaft."
    )


def generate_credentials_key(_: argparse.Namespace) -> None:
    print(generate_credential_key())


def configure_yazio(args: argparse.Namespace) -> None:
    username = args.username or input("CaloGraph-Benutzername: ").strip()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if not user:
            raise SystemExit("CaloGraph-Benutzer nicht gefunden.")

    email = args.email or input("YAZIO-E-Mail-Adresse: ").strip()
    password = getpass.getpass("YAZIO-Passwort (wird verschlüsselt gespeichert): ")
    if not email or not password:
        raise SystemExit("YAZIO-E-Mail-Adresse und Passwort sind erforderlich.")
    try:
        validate_yazio_credentials(
            email,
            password,
            operation_key=user.id,
        )
        connection = configure_yazio_connection(
            user,
            email,
            password,
            sync_interval_minutes=args.interval_hours * 60,
            sync_days=args.days,
        )
    except CredentialEncryptionError as exc:
        raise SystemExit(
            f"{exc} Zuerst CREDENTIAL_ENCRYPTION_KEY in .env setzen."
        ) from exc
    except (ValueError, YazioSyncError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"Automatischer YAZIO-Sync für '{username}' aktiviert: "
        f"alle {connection.sync_interval_minutes // 60} Stunden, "
        f"jeweils {connection.sync_days} Tage."
    )


def disable_yazio(args: argparse.Namespace) -> None:
    username = args.username or input("CaloGraph-Benutzername: ").strip()
    with SessionLocal() as db:
        connection = db.scalar(
            select(YazioConnection)
            .join(User, User.id == YazioConnection.user_id)
            .where(User.username == username)
        )
        if connection is None:
            raise SystemExit("Für diesen Benutzer ist keine YAZIO-Verbindung eingerichtet.")
        connection.sync_enabled = False
        db.commit()
    print(f"Automatischer YAZIO-Sync für '{username}' deaktiviert.")


def yazio_status(args: argparse.Namespace) -> None:
    username = args.username or input("CaloGraph-Benutzername: ").strip()
    with SessionLocal() as db:
        connection = db.scalar(
            select(YazioConnection)
            .join(User, User.id == YazioConnection.user_id)
            .where(User.username == username)
        )
        if connection is None:
            print("Keine YAZIO-Verbindung eingerichtet.")
            return
        print(f"Aktiv: {'ja' if connection.sync_enabled else 'nein'}")
        print(f"Intervall: {connection.sync_interval_minutes // 60} Stunden")
        print(f"Abrufzeitraum: {connection.sync_days} Tage")
        print(f"Letzter Versuch: {connection.last_attempt_at or 'noch keiner'}")
        print(f"Letzter Erfolg: {connection.last_success_at or 'noch keiner'}")
        print(f"Nächster Lauf: {connection.next_sync_at or 'sofort'}")
        print(f"Letzter Fehler: {connection.last_error or 'keiner'}")


def run_yazio_scheduler(args: argparse.Namespace) -> None:
    try:
        settings.validate_runtime_security()
    except ProductionConfigurationError as exc:
        raise SystemExit(str(exc)) from None
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info(
        "yazio_scheduler_started poll_seconds=%s jitter_minutes=%s",
        settings.yazio_scheduler_poll_seconds,
        settings.yazio_scheduler_jitter_minutes,
    )
    while True:
        try:
            attempted, succeeded = run_due_yazio_syncs()
        except Exception:
            logger.exception("yazio_scheduler_cycle_failed")
            if args.once:
                raise
            attempted = succeeded = 0
        if attempted:
            logger.info(
                "yazio_scheduler_cycle attempted=%s succeeded=%s",
                attempted,
                succeeded,
            )
        Path("/tmp/yazio-scheduler-heartbeat").touch()
        if args.once:
            print(f"{attempted} fällige Verbindung(en), {succeeded} erfolgreich.")
            return
        time.sleep(settings.yazio_scheduler_poll_seconds)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m app.cli")
    commands = root.add_subparsers(dest="command", required=True)
    user = commands.add_parser("create-user")
    user.add_argument("--username")
    user.add_argument("--password", help=argparse.SUPPRESS)
    user.add_argument("--timezone", default="Europe/Berlin")
    user.add_argument("--if-not-exists", action="store_true")
    user.add_argument("--admin", action="store_true")
    user.add_argument("--raw-retention-days", type=int, default=settings.raw_payload_retention_days)
    user.set_defaults(handler=create_user)
    token = commands.add_parser("create-import-token")
    token.add_argument("--username")
    token.add_argument("--label")
    token.set_defaults(handler=create_token)
    seed = commands.add_parser("seed-demo-data")
    seed.add_argument("--username", default="admin")
    seed.set_defaults(handler=seed_demo)
    purge = commands.add_parser("purge-raw-imports")
    purge.set_defaults(handler=purge_raw)
    yazio = commands.add_parser(
        "sync-yazio",
        help="Experimenteller Direktabruf über die inoffizielle YAZIO-Schnittstelle",
    )
    yazio.add_argument("--username", default="admin")
    yazio.add_argument("--email")
    yazio.add_argument("--from-date", help="Startdatum im Format YYYY-MM-DD")
    yazio.add_argument("--end-date", help="Enddatum im Format YYYY-MM-DD")
    yazio.add_argument(
        "--days",
        type=int,
        default=60,
        help="Ohne --from-date werden die letzten N Tage abgerufen (Standard: 60)",
    )
    yazio.add_argument(
        "--source-identifier",
        help="Stabile, nicht sensible Kennung bei mehreren YAZIO-Konten",
    )
    yazio.set_defaults(handler=sync_yazio)
    credential_key = commands.add_parser(
        "generate-credential-key",
        help="Neuen Schlüssel für verschlüsselte Zugangsdaten erzeugen",
    )
    credential_key.set_defaults(handler=generate_credentials_key)
    configure = commands.add_parser(
        "configure-yazio",
        help="Verschlüsselte YAZIO-Verbindung für automatische Synchronisierung einrichten",
    )
    configure.add_argument("--username", default="admin")
    configure.add_argument("--email")
    configure.add_argument(
        "--interval-hours",
        type=int,
        default=6,
        choices=range(1, 169),
        metavar="1-168",
    )
    configure.add_argument(
        "--days",
        type=int,
        default=7,
        choices=range(1, 367),
        metavar="1-366",
    )
    configure.set_defaults(handler=configure_yazio)
    disable = commands.add_parser(
        "disable-yazio",
        help="Automatische YAZIO-Synchronisierung deaktivieren",
    )
    disable.add_argument("--username", default="admin")
    disable.set_defaults(handler=disable_yazio)
    status = commands.add_parser(
        "yazio-status",
        help="Status der automatischen YAZIO-Synchronisierung anzeigen",
    )
    status.add_argument("--username", default="admin")
    status.set_defaults(handler=yazio_status)
    scheduler = commands.add_parser(
        "run-yazio-scheduler",
        help="Fällige verschlüsselte YAZIO-Verbindungen synchronisieren",
    )
    scheduler.add_argument("--once", action="store_true")
    scheduler.set_defaults(handler=run_yazio_scheduler)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        args.handler(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
