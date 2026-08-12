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
from sqlalchemy.orm import Session

from app.auth.password_policy import (
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    validate_new_password,
)
from app.auth.security import create_api_token, hash_password, purge_expired_sessions
from app.config import ProductionConfigurationError, settings
from app.database import SessionLocal
from app.importers.common import CanonicalSample
from app.importers.json_adapter import AdapterResult
from app.models import NutritionTarget, TrackingQualitySettings, User, YazioConnection
from app.security_events import log_security_event, security_reference
from app.services.account_recovery import purge_account_recovery_tokens
from app.services.admin_reauth import (
    AdminReauthenticationRejected,
    verify_admin_reauthentication,
)
from app.services.credential_crypto import (
    CredentialEncryptionError,
    generate_credential_key,
)
from app.services.import_service import persist_import, purge_expired_raw_payloads
from app.services.passkeys import purge_expired_webauthn_challenges
from app.services.rate_limit import RateLimitExceeded, purge_expired_rate_limit_buckets
from app.services.user_lifecycle import (
    UserLifecycleRejected,
    issue_account_recovery,
    reset_user_authenticators,
)
from app.services.user_operation_lock import (
    InactiveUserOperation,
    UserOperationBusy,
    shared_user_operation,
)
from app.services.yazio_sync import (
    YazioConnectionNotConfigured,
    YazioSyncError,
    configure_yazio_connection,
    effective_sync_days,
    effective_sync_interval_minutes,
    enqueue_historical_yazio_sync,
    run_due_yazio_syncs,
    sync_yazio_user,
    validate_yazio_credentials,
)

logger = logging.getLogger("calograph.cli")


def create_user(args: argparse.Namespace) -> None:
    username = args.username or input("Benutzername: ").strip()
    password = args.password or getpass.getpass(
        f"Passwort (mindestens {MIN_PASSWORD_LENGTH} Zeichen): "
    )
    try:
        validate_new_password(password, username)
    except PasswordPolicyError as exc:
        raise SystemExit(str(exc)) from None
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
        db.commit()
        user_id = user.id
    log_security_event(
        "admin.user.created",
        target_ref=security_reference("user", user_id),
    )
    print(f"Benutzer '{username}' wurde angelegt.")


def create_token(args: argparse.Namespace) -> None:
    username = args.username or input("Benutzername: ").strip()
    label = args.label or input("Bezeichnung des Import-Clients: ").strip()
    try:
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.username == username))
            if not user:
                raise SystemExit("Benutzer nicht gefunden.")
            token, raw = create_api_token(db, user, label)
            user_id = user.id
            token_id = token.id
    except InactiveUserOperation as exc:
        raise SystemExit(
            "Für einen inaktiven Benutzer kann kein Import-Token erstellt werden."
        ) from exc
    except UserOperationBusy as exc:
        raise SystemExit("Für diesen Benutzer läuft gerade eine administrative Operation.") from exc
    log_security_event(
        "auth.api_token.created",
        actor_ref=security_reference("user", user_id),
        target_ref=security_reference("api_token", token_id),
    )
    print("Import-Token (wird nur dieses eine Mal angezeigt):")
    print(raw)


def _reauthenticate_cli_admin(
    db: Session,
    args: argparse.Namespace,
) -> User:
    admin_username = (
        getattr(args, "admin_username", None) or input("Administrator-Benutzername: ").strip()
    )
    admin = db.scalar(select(User).where(User.username == admin_username))
    if admin is None:
        raise SystemExit("Administrator-Reauthentifizierung fehlgeschlagen.")
    password = getattr(args, "admin_password", None) or getpass.getpass(
        "Aktuelles Administrator-Passwort: "
    )
    code = getattr(args, "code", None)
    if code is None:
        code = getpass.getpass("Administrator-MFA-Code (falls aktiviert): ").strip() or None
    try:
        verify_admin_reauthentication(db, admin.id, password, code)
    except (AdminReauthenticationRejected, RateLimitExceeded) as exc:
        raise SystemExit("Administrator-Reauthentifizierung fehlgeschlagen.") from exc
    return admin


def reset_authenticators(args: argparse.Namespace) -> None:
    username = args.username or input("Benutzername: ").strip()
    if args.confirm != username:
        raise SystemExit(
            "Authenticator-Rücksetzung abgebrochen. "
            "--confirm muss exakt dem Benutzernamen entsprechen."
        )
    with SessionLocal() as db:
        admin = _reauthenticate_cli_admin(db, args)
        target = db.scalar(select(User).where(User.username == username))
        if target is None:
            raise SystemExit("Benutzer nicht gefunden.")
        try:
            reset_user_authenticators(db, admin.id, target.id)
        except UserLifecycleRejected as exc:
            messages = {
                "not_admin": "Aktive Administratorrechte erforderlich.",
                "self_action": "Die Aktion auf dem eigenen Konto ist nicht erlaubt.",
                "target_active": "Der Benutzer muss zuerst deaktiviert werden.",
                "target_missing": "Benutzer nicht gefunden.",
                "operation_busy": "Für diesen Benutzer läuft eine administrative Operation.",
                "last_admin": "Der letzte aktive Administrator muss erhalten bleiben.",
                "target_confirmation": "Die Bestätigung des Benutzernamens ist ungültig.",
            }
            raise SystemExit(messages[exc.reason]) from exc
    print(
        f"Authentikatoren für '{username}' wurden zurückgesetzt; "
        "Sitzungen und API-Tokens wurden widerrufen."
    )


def issue_recovery(args: argparse.Namespace) -> None:
    username = args.username or input("Benutzername: ").strip()
    with SessionLocal() as db:
        admin = _reauthenticate_cli_admin(db, args)
        target = db.scalar(select(User).where(User.username == username))
        if target is None:
            raise SystemExit("Benutzer nicht gefunden.")
        try:
            recovery_token, raw_token = issue_account_recovery(db, admin.id, target.id)
        except UserLifecycleRejected as exc:
            raise SystemExit(f"Recovery konnte nicht ausgestellt werden: {exc.reason}.") from exc
    print(
        f"Recovery-Token (nur einmal sichtbar, gültig bis {recovery_token.expires_at.isoformat()}):"
    )
    print(raw_token)


def seed_demo(args: argparse.Namespace) -> None:
    try:
        with SessionLocal() as db:
            stored_user = db.scalar(select(User).where(User.username == args.username))
            if not stored_user:
                raise SystemExit("Benutzer nicht gefunden. Zuerst create-user ausführen.")
            with shared_user_operation(db, stored_user.id) as user:
                start = date(2026, 2, 15)
                change_day = start + timedelta(days=60)
                existing_target = db.scalar(
                    select(NutritionTarget).where(
                        NutritionTarget.user_id == user.id,
                        NutritionTarget.valid_from == start,
                    )
                )
                if not existing_target:
                    current = db.scalar(
                        select(NutritionTarget)
                        .where(
                            NutritionTarget.user_id == user.id,
                            NutritionTarget.valid_to.is_(None),
                        )
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
                    db,
                    user,
                    result,
                    None,
                    "application/x-synthetic",
                    "seed-demo-data",
                )
    except InactiveUserOperation as exc:
        raise SystemExit(
            "Für einen inaktiven Benutzer können keine Demodaten angelegt werden."
        ) from exc
    except UserOperationBusy as exc:
        raise SystemExit("Für diesen Benutzer läuft gerade eine administrative Operation.") from exc
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

    log_security_event(
        "integration.yazio.sync_completed",
        actor_ref=security_reference("user", user.id),
        details={
            "mode": "manual",
            "received": summary.received,
            "inserted": summary.inserted,
            "updated": summary.updated,
            "skipped": summary.skipped,
            "failed": summary.failed,
        },
    )

    print(
        f"YAZIO-Sync {start_day.isoformat()} bis {end_day.isoformat()}: "
        f"{summary.inserted} neu, {summary.updated} aktualisiert, "
        f"{summary.skipped} unverändert, {summary.failed} fehlerhaft."
    )


def generate_credentials_key(_: argparse.Namespace) -> None:
    print(generate_credential_key())


def configure_yazio(args: argparse.Namespace) -> None:
    username = args.username or input("CaloGraph-Benutzername: ").strip()
    try:
        with SessionLocal() as db:
            user = db.scalar(select(User).where(User.username == username))
            if not user:
                raise SystemExit("CaloGraph-Benutzer nicht gefunden.")
            email = args.email or input("YAZIO-E-Mail-Adresse: ").strip()
            password = getpass.getpass("YAZIO-Passwort (wird verschlüsselt gespeichert): ")
            if not email or not password:
                raise SystemExit("YAZIO-E-Mail-Adresse und Passwort sind erforderlich.")
            with shared_user_operation(db, user.id):
                validate_yazio_credentials(
                    email,
                    password,
                    operation_key=user.id,
                )
                connection = configure_yazio_connection(
                    user,
                    email,
                    password,
                    sync_interval_minutes=(
                        args.interval_hours * 60
                        if args.interval_hours is not None
                        else None
                    ),
                    sync_days=args.days,
                    start_day=(
                        date.fromisoformat(args.from_date)
                        if args.from_date is not None
                        else None
                    ),
                    end_day=(
                        date.fromisoformat(args.end_date)
                        if args.end_date is not None
                        else None
                    ),
                )
    except CredentialEncryptionError as exc:
        raise SystemExit(f"{exc} Zuerst CREDENTIAL_ENCRYPTION_KEY in .env setzen.") from exc
    except (InactiveUserOperation, UserOperationBusy) as exc:
        raise SystemExit(f"YAZIO-Verbindung nicht konfiguriert: {exc}") from exc
    except (ValueError, YazioSyncError) as exc:
        raise SystemExit(str(exc)) from exc
    log_security_event(
        "integration.yazio.connection_configured",
        actor_ref=security_reference("user", user.id),
        target_ref=security_reference("yazio_connection", connection.id),
    )
    print(
        f"Automatischer YAZIO-Sync für '{username}' aktiviert: "
        f"alle {effective_sync_interval_minutes(connection) // 60} Stunden, "
        f"jeweils {effective_sync_days(connection)} Tage."
    )


def disable_yazio(args: argparse.Namespace) -> None:
    username = args.username or input("CaloGraph-Benutzername: ").strip()
    try:
        with SessionLocal() as db:
            connection = db.scalar(
                select(YazioConnection)
                .join(User, User.id == YazioConnection.user_id)
                .where(User.username == username)
            )
            if connection is None:
                raise SystemExit("Für diesen Benutzer ist keine YAZIO-Verbindung eingerichtet.")
            with shared_user_operation(db, connection.user_id):
                connection.sync_enabled = False
                db.commit()
                connection_id = connection.id
                user_id = connection.user_id
    except (InactiveUserOperation, UserOperationBusy) as exc:
        raise SystemExit(f"YAZIO-Verbindung nicht deaktiviert: {exc}") from exc
    log_security_event(
        "integration.yazio.connection_disabled",
        actor_ref=security_reference("user", user_id),
        target_ref=security_reference("yazio_connection", connection_id),
    )
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
        print(f"Intervall: {effective_sync_interval_minutes(connection) // 60} Stunden")
        print(f"Abrufzeitraum: {effective_sync_days(connection)} Tage")
        print(f"Historischer Abruf: {connection.historical_sync_state}")
        print(f"Letzter Versuch: {connection.last_attempt_at or 'noch keiner'}")
        print(f"Letzter Erfolg: {connection.last_success_at or 'noch keiner'}")
        print(f"Nächster Lauf: {connection.next_sync_at or 'sofort'}")
        print(f"Letzter Fehler: {connection.last_error or 'keiner'}")


def queue_yazio_history(args: argparse.Namespace) -> None:
    username = args.username or input("CaloGraph-Benutzername: ").strip()
    try:
        start_day = date.fromisoformat(args.from_date)
        end_day = date.fromisoformat(args.end_date)
    except ValueError as exc:
        raise SystemExit("Datumsangaben müssen das Format YYYY-MM-DD verwenden.") from exc
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit("CaloGraph-Benutzer nicht gefunden.")
        user_id = user.id
    try:
        connection = enqueue_historical_yazio_sync(
            user_id,
            start_day=start_day,
            end_day=end_day,
        )
    except (ValueError, YazioConnectionNotConfigured, YazioSyncError) as exc:
        raise SystemExit(str(exc)) from exc
    log_security_event(
        "integration.yazio.history_queued",
        actor_ref=security_reference("user", user_id),
        target_ref=security_reference("yazio_connection", connection.id),
        details={"mode": "range"},
    )
    print("Historischer YAZIO-Zeitraum wurde für den Scheduler vorgemerkt.")


def _touch_yazio_scheduler_heartbeat() -> None:
    Path("/tmp/yazio-scheduler-heartbeat").touch()


def run_yazio_scheduler(args: argparse.Namespace) -> None:
    try:
        settings.validate_runtime_security("scheduler")
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
    last_security_cleanup = 0.0
    while True:
        try:
            _touch_yazio_scheduler_heartbeat()
            attempted, succeeded = run_due_yazio_syncs(
                after_connection=_touch_yazio_scheduler_heartbeat
            )
            monotonic_now = time.monotonic()
            if monotonic_now - last_security_cleanup >= 3600:
                with SessionLocal() as db:
                    rate_limit_deleted = purge_expired_rate_limit_buckets(
                        db,
                        settings.rate_limit_retention_hours,
                    )
                    recovery_token_deleted = purge_account_recovery_tokens(db)
                    session_deleted = purge_expired_sessions(db)
                    webauthn_challenge_deleted = purge_expired_webauthn_challenges(db)
                last_security_cleanup = monotonic_now
                if rate_limit_deleted:
                    logger.info(
                        "rate_limit_cleanup deleted=%s",
                        rate_limit_deleted,
                    )
                if session_deleted:
                    logger.info("session_cleanup deleted=%s", session_deleted)
                if webauthn_challenge_deleted:
                    logger.info(
                        "webauthn_challenge_cleanup deleted=%s",
                        webauthn_challenge_deleted,
                    )
                if recovery_token_deleted:
                    logger.info(
                        "account_recovery_token_cleanup deleted=%s",
                        recovery_token_deleted,
                    )
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
        _touch_yazio_scheduler_heartbeat()
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
    authenticator_reset = commands.add_parser("reset-authenticators")
    authenticator_reset.add_argument("--username")
    authenticator_reset.add_argument("--admin-username", default="admin")
    authenticator_reset.add_argument("--confirm", required=True)
    authenticator_reset.set_defaults(handler=reset_authenticators)
    recovery = commands.add_parser("issue-account-recovery")
    recovery.add_argument("--username")
    recovery.add_argument("--admin-username", default="admin")
    recovery.set_defaults(handler=issue_recovery)
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
        choices=range(1, 169),
        metavar="1-168",
        help="Individuelles Intervall; ohne Angabe gilt YAZIO_SYNC_INTERVAL_HOURS.",
    )
    configure.add_argument(
        "--days",
        type=int,
        choices=range(1, 367),
        metavar="1-366",
        help="Individueller Zeitraum; ohne Angabe gilt YAZIO_SYNC_DAYS.",
    )
    configure.add_argument("--from-date", help="Startdatum YYYY-MM-DD")
    configure.add_argument("--end-date", help="Enddatum YYYY-MM-DD")
    configure.set_defaults(handler=configure_yazio)
    disable = commands.add_parser(
        "disable-yazio",
        help="Automatische YAZIO-Synchronisierung deaktivieren",
    )
    history = commands.add_parser(
        "sync-yazio-history",
        help="Historischen YAZIO-Zeitraum für den Scheduler vormerken",
    )
    history.add_argument("--username", default="admin")
    history.add_argument("--from-date", required=True, help="Startdatum YYYY-MM-DD")
    history.add_argument("--end-date", required=True, help="Enddatum YYYY-MM-DD")
    history.set_defaults(handler=queue_yazio_history)
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
