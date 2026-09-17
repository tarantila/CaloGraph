from __future__ import annotations

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


def _single_postgres_image(path: Path) -> str:
    images = re.findall(
        r"^\s*image:\s*(postgres:\S+)\s*$",
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert len(images) == 1, f"expected exactly one PostgreSQL image in {path}"
    return images[0]


def test_postgres_minor_pin_is_consistent_and_watched() -> None:
    """Keep the deliberate PostgreSQL pin and the update mechanism coupled."""
    compose_image = _single_postgres_image(ROOT / "docker-compose.yml")
    test_compose_image = _single_postgres_image(ROOT / "docker-compose.test.yml")
    ci_image = _single_postgres_image(ROOT / ".github/workflows/ci.yml")

    assert EXACT_MINOR_TAG.fullmatch(compose_image), (
        "docker-compose.yml must pin PostgreSQL to an exact minor release"
    )
    assert EXACT_MINOR_TAG.fullmatch(test_compose_image), (
        "docker-compose.test.yml must pin PostgreSQL to an exact minor release"
    )
    assert PINNED_IMAGE.fullmatch(ci_image), (
        "the GitHub Actions PostgreSQL service must use the same exact minor and a digest"
    )

    ci_tag = ci_image.split("@", maxsplit=1)[0]
    assert compose_image == test_compose_image == ci_tag, (
        "runtime, Compose integration tests, and GitHub Actions must test the same "
        "PostgreSQL minor release"
    )

    dependabot = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
    assert re.search(
        r'^\s*-\s*package-ecosystem:\s*["\']?docker-compose["\']?\s*$',
        dependabot,
        flags=re.MULTILINE,
    ), "the exact PostgreSQL pin must be watched by Dependabot's docker-compose ecosystem"
