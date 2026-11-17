"""Exercise Review startup output with synthetic secrets and no Docker access."""

import json
import os
import pty
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.services.analysis_demo import ANALYSIS_DEMO_USERNAME, demo_user_id

ROOT = next(
    root for root in (Path(__file__).resolve().parents[2], Path("/workspace"))
    if (root / "scripts/analysis-review.sh").is_file()
)


@pytest.fixture
def review_harness(tmp_path):
    scripts = tmp_path / "project/scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/analysis-review.sh", scripts / "analysis-review.sh")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # The supported host entrypoint uses GNU realpath. Alpine's test image has
    # BusyBox instead; supply equivalent -m resolution for this harness only.
    realpath = bindir / "realpath"
    realpath.write_text(
        '#!/usr/bin/env python3\n'
        'import pathlib, sys\n'
        'assert sys.argv[1:3] == ["-m", "--"]\n'
        'print(pathlib.Path(sys.argv[3]).resolve())\n'
    )
    realpath.chmod(0o700)
    docker = bindir / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ["CAPTURE_ROOT"])
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args[:2] == ["volume", "inspect"]:
    if "--format" in args:
        print("postgres-data" if '"com.docker.compose.volume"' in args[3] else "calograph-analysis-v2-review")
elif args[0] == "compose":
    if "-c" in args:
        query = args[args.index("-c") + 1]
        if query == "SELECT current_database()":
            print("calograph_analysis_v2_test")
        elif query.startswith("SELECT id::text"):
            if os.environ["DEMO_PRESENT"] == "1" or (root / "seeded").exists():
                print(os.environ["DEMO_ID"] + ":" + os.environ["DEMO_USERNAME"])
        elif query == "SELECT count(*) FROM users":
            print("0")
        elif "demo_user_id" in query:
            print(os.environ["DEMO_ID"])
    elif "seed-analysis-demo" in args:
        (root / "seeded").touch()
''')
    docker.chmod(0o700)
    curl = bindir / "curl"
    curl.write_text('#!/bin/sh\nexit "${HEALTH_EXIT:-0}"\n')
    curl.chmod(0o700)
    # The production path guard rejects /tmp. Keep disposable test-only secrets
    # in shared memory instead; never read the operator's persistent secrets.
    with tempfile.TemporaryDirectory(prefix="calograph-review-test-", dir="/dev/shm") as state:
        secrets = Path(state) / "calograph/analysis-review/secrets"
        secrets.mkdir(parents=True)
        for name in ("postgres_password", "session_secret", "rate_limit_secret",
                     "credential_encryption_key", "mfa_encryption_key"):
            secret = secrets / name
            secret.write_text(f"synthetic-do-not-print-{name}")
            secret.chmod(0o600)
        yield {
            "script": scripts / "analysis-review.sh",
            "root": tmp_path,
            "secrets": secrets,
            "env": {
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "HOME": str(tmp_path),
                "XDG_DATA_HOME": state,
                "CAPTURE_ROOT": str(tmp_path),
                "DEMO_ID": str(demo_user_id()),
                "DEMO_USERNAME": ANALYSIS_DEMO_USERNAME,
            },
        }


@pytest.mark.parametrize("demo_present", [False, True], ids=["initial-seed", "restart"])
def test_successful_start_shows_accounts_without_secrets(review_harness, demo_present):
    h = review_harness
    h["env"]["DEMO_PRESENT"] = "1" if demo_present else "0"
    master, slave = pty.openpty()
    try:
        result = subprocess.run(
            ["sh", str(h["script"]), "up"], env=h["env"], stdin=slave,
            capture_output=True, text=True, timeout=20, check=False,
        )
    finally:
        os.close(slave)
        os.close(master)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "Analysis-Review bereit: http://localhost:8289\n"
        "\nReview-Zugänge:\n"
        "  Demo:  calograph-analysis-demo\n"
        "  Admin: calograph-analysis-review-admin\n"
        "\nPasswörter werden aus Sicherheitsgründen nicht angezeigt.\n"
    )
    assert result.stderr == ""
    assert str(demo_user_id()) not in result.stdout
    assert str(h["secrets"]) not in result.stdout
    for secret in h["secrets"].iterdir():
        assert secret.read_text() not in result.stdout + result.stderr
    calls = [json.loads(line) for line in (h["root"] / "calls.jsonl").read_text().splitlines()]
    seeds = [args for args in calls if "seed-analysis-demo" in args]
    creates = [args for args in calls if "create-user" in args]
    assert len(seeds) == (0 if demo_present else 1)
    assert len(creates) == (0 if demo_present else 1)
    if creates:
        assert creates[0][creates[0].index("--username") + 1] == "calograph-analysis-review-admin"


def test_failed_start_does_not_claim_accounts_are_ready(review_harness):
    h = review_harness
    h["env"].update(DEMO_PRESENT="1", HEALTH_EXIT="7")
    result = subprocess.run(
        ["sh", str(h["script"]), "up"], env=h["env"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode != 0
    assert "Analysis-Review bereit" not in result.stdout
    assert "Review-Zugänge" not in result.stdout
