from __future__ import annotations

import ast
import re
from pathlib import Path


def _repository_root() -> Path:
    candidates = (Path(__file__).resolve().parents[2], Path("/workspace"))
    for candidate in candidates:
        if (candidate / "docker-compose.yml").is_file() and (
            candidate / ".github" / "workflows" / "ci.yml"
        ).is_file():
            return candidate
    raise AssertionError("unable to locate the repository root")


ROOT = _repository_root()
EXACT_MINOR_TAG = re.compile(r"^postgres:\d+\.\d+-alpine$")
PINNED_IMAGE = re.compile(r"^postgres:\d+\.\d+-alpine@sha256:[0-9a-f]{64}$")


def _postgres_images(path: Path) -> list[str]:
    images = re.findall(
        r"^\s*image:\s*(postgres:\S+)\s*$",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert images, f"expected PostgreSQL images in {path}"
    return images


def test_postgres_minor_pin_is_consistent_and_watched() -> None:
    """Keep the deliberate PostgreSQL pin and the update mechanism coupled."""
    compose_images = _postgres_images(ROOT / "docker-compose.yml")
    development_images = _postgres_images(ROOT / "docker-compose-dev.yml")
    ci_images = _postgres_images(ROOT / ".github/workflows/ci.yml")
    assert len(compose_images) == len(ci_images) == 1
    assert len(development_images) >= 4, "dev, test, E2E and Review databases must be pinned"
    for image in compose_images + development_images:
        assert EXACT_MINOR_TAG.fullmatch(image), (
            "all Compose PostgreSQL services must pin an exact minor release"
        )
    assert PINNED_IMAGE.fullmatch(ci_images[0]), (
        "the GitHub Actions PostgreSQL service must use the same exact minor and a digest"
    )
    ci_tag = ci_images[0].split("@", maxsplit=1)[0]
    assert all(image == ci_tag for image in compose_images + development_images), (
        "all runtime and integration PostgreSQL services must test the same minor release"
    )

    restore_script = (ROOT / "scripts/isolated-restore-test.sh").read_text(encoding="utf-8")
    restore_default = re.search(
        r"BACKUP_RESTORE_POSTGRES_IMAGE:-(postgres:[^}]+)", restore_script,
    )
    assert restore_default is not None, "the isolated restore must have an explicit default image"
    assert restore_default.group(1) == ci_tag, (
        "the default isolated restore must use the runtime PostgreSQL release"
    )
    smoke_script = (ROOT / "scripts/production-smoke.sh").read_text(encoding="utf-8")
    for smoke_image in re.findall(r"BACKUP_RESTORE_POSTGRES_IMAGE=(postgres:\S+)", smoke_script):
        assert smoke_image == ci_tag, "smoke restore overrides must use the runtime release"

    dependabot = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    assert re.search(
        r'^\s*-\s*package-ecosystem:\s*["\']?docker-compose["\']?\s*$',
        dependabot,
        flags=re.MULTILINE,
    ), "the exact PostgreSQL pin must be watched by Dependabot's docker-compose ecosystem"


def test_all_postgres_regression_modules_are_selected_by_ci_and_compose() -> None:
    """Use one discovery rule so new isolated PostgreSQL tests cannot silently drift."""
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose-dev.yml").read_text(encoding="utf-8")
    discovery = "tests/test_*_postgres.py"
    ci_job = ci.split("\n  backend-postgres:\n", maxsplit=1)[1].split(
        "\n  frontend:\n", maxsplit=1
    )[0]
    compose_service = compose.split("\n  backend-postgres-ci:\n", maxsplit=1)[1].split(
        "\n  e2e-postgres:\n", maxsplit=1
    )[0]
    assert f"run: uv run pytest {discovery}" in ci_job
    assert f"alembic upgrade head && pytest {discovery}" in compose_service


def test_opted_in_postgres_tests_use_discoverable_module_names() -> None:
    """Real opt-in gates must live in modules selected by the shared test glob."""
    tests_dir = Path(__file__).resolve().parent
    test_modules = sorted(tests_dir.glob("test_*.py"))
    assert test_modules, f"expected backend test modules in {tests_dir}"
    gated_modules = []
    for path in test_modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.Name) and node.id == "POSTGRES_TESTS_ENABLED"
            for node in ast.walk(tree)
        ) and not path.name.endswith("_postgres.py"):
            gated_modules.append(path.name)

    assert not gated_modules, (
        "PostgreSQL opt-in tests must be discovered by tests/test_*_postgres.py: "
        f"{gated_modules}"
    )
