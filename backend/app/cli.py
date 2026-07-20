import argparse
import getpass
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.auth.security import create_api_token, hash_password
from app.config import settings
from app.database import SessionLocal
from app.importers.common import CanonicalSample
from app.importers.json_adapter import AdapterResult
from app.models import NutritionTarget, TrackingQualitySettings, User
from app.services.import_service import persist_import, purge_expired_raw_payloads


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
        user = User(
            username=username,
            password_hash=hash_password(password),
            timezone=args.timezone,
            raw_payload_retention_days=args.raw_retention_days,
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
        incomplete = offset % 17 == 0
        if incomplete:
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
            ("active_energy_kcal", Decimal(randomizer.randint(250, 750)), "kcal"),
            ("steps", Decimal(randomizer.randint(3500, 14500)), "count"),
            (
                "weight_kg",
                Decimal("84.0")
                - Decimal(offset) * Decimal("0.025")
                + Decimal(randomizer.randint(-2, 2)) / 10,
                "kg",
            ),
        ]
        if incomplete:
            metrics = metrics[:2] + metrics[4:]
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


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="python -m app.cli")
    commands = root.add_subparsers(dest="command", required=True)
    user = commands.add_parser("create-user")
    user.add_argument("--username")
    user.add_argument("--password", help=argparse.SUPPRESS)
    user.add_argument("--timezone", default="Europe/Berlin")
    user.add_argument("--if-not-exists", action="store_true")
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
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        args.handler(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
