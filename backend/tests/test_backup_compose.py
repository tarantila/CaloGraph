"""Exercise backup Compose boundaries without Docker, secrets, or runtime access."""

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = next(
    root for root in (Path(__file__).resolve().parents[2], Path("/workspace"))
    if (root / "docker-compose.yml").is_file()
)


@pytest.fixture
def backup_harness(tmp_path: Path) -> dict:
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)
    for name in ("backup-compose.sh", "restore-postgres.sh"):
        source = ROOT / "scripts" / name
        if source.exists():
            shutil.copyfile(source, scripts / name)
            (scripts / name).chmod(0o700)
    (project / "docker-compose.yml").write_text("services: {}\n")
    (scripts / "verify-backup.sh").write_text("#!/bin/sh\nexit 0\n")
    (scripts / "verify-backup.sh").chmod(0o700)
    source_file = tmp_path / "environment $literal.env"
    source_file.write_text("SYNTHETIC=do-not-print\n")
    source_dir = tmp_path / "secrets $literal"
    source_dir.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    docker = bindir / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, signal, sys, time\n"
        "args = sys.argv[1:]\n"
        "root = pathlib.Path(os.environ['CAPTURE_ROOT'])\n"
        "if args[0] == 'inspect':\n"
        "    print(os.environ['INSPECT_JSON'])\n"
        "    sys.exit(0)\n"
        "if '--environment' in args:\n"
        "    print(os.environ.get('COMPOSE_TEST_ENVIRONMENT', ''))\n"
        "    sys.exit(0)\n"
        "with (root / 'calls.jsonl').open('a') as stream:\n"
        "    stream.write(json.dumps(args) + '\\n')\n"
        "files = [args[i+1] for i, arg in enumerate(args[:-1]) if arg == '-f']\n"
        "if len(files) == 2:\n"
        "    path = pathlib.Path(files[1])\n"
        "    (root / 'override.json').write_text(path.read_text())\n"
        "    (root / 'metadata.json').write_text(json.dumps({\n"
        "        'path': str(path), 'mode': path.stat().st_mode & 0o777,\n"
        "        'directory_mode': path.parent.stat().st_mode & 0o777}))\n"
        "    if os.environ.get('CAPTURE_STDIN'):\n"
        "        (root / 'stdin').write_bytes(sys.stdin.buffer.read())\n"
        "    if os.environ.get('DOCKER_WAIT'):\n"
        "        (root / 'ready').touch()\n"
        "        while True: time.sleep(0.02)\n"
        "if 'ps' in args:\n"
        "    print('synthetic-agent' if '-q' in args or '-aq' in args else 'backup-agent')\n"
        "sys.exit(int(os.environ.get('DOCKER_EXIT', '0')))\n"
    )
    docker.chmod(0o700)
    environment = {
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "CAPTURE_ROOT": str(tmp_path),
        "BACKUP_SECRETS_SOURCE_FILE": str(source_file),
        "BACKUP_SECRETS_SOURCE_DIR": str(source_dir),
    }
    return {
        "root": tmp_path,
        "project": project,
        "script": scripts / "backup-compose.sh",
        "file": source_file,
        "directory": source_dir,
        "environment": environment,
    }


def invoke(harness: dict, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(harness["script"]), *arguments],
        cwd=harness["project"],
        env=harness["environment"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_wrapper_emits_only_additive_private_readonly_mounts_and_cleans_up(
    backup_harness: dict,
) -> None:
    h = backup_harness
    result = invoke(h, "--profile", "backup", "up", "-d", "backup-agent")
    assert result.returncode == 0, result.stderr
    override = json.loads((h["root"] / "override.json").read_text())
    assert set(override) == {"services"}
    assert set(override["services"]) == {"backup-agent"}
    service = override["services"]["backup-agent"]
    assert set(service) == {"environment", "volumes"}
    assert service["environment"] == {
        "BACKUP_INCLUDE_SECRETS": "true",
        "SECRETS_SOURCE_FILE": "/backup-input/environment.env",
        "SECRETS_SOURCE_DIR": "/backup-input/secrets",
    }
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": str(h["file"]).replace("$", "$$"),
            "target": "/backup-input/environment.env",
            "read_only": True,
            "bind": {"create_host_path": False},
        },
        {
            "type": "bind",
            "source": str(h["directory"]).replace("$", "$$"),
            "target": "/backup-input/secrets",
            "read_only": True,
            "bind": {"create_host_path": False},
        },
    ]
    metadata = json.loads((h["root"] / "metadata.json").read_text())
    assert metadata["mode"] == 0o600
    assert metadata["directory_mode"] == 0o700
    assert not Path(metadata["path"]).parent.exists()
    assert "do-not-print" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["missing-file", "missing-directory", "file-is-directory", "directory-is-file", "file-link", "directory-link", "parent-link", "traversed-link"])
def test_wrapper_rejects_invalid_sources_before_docker_action(
    backup_harness: dict, failure: str,
) -> None:
    h = backup_harness
    env = h["environment"]
    root = h["root"]
    if failure == "missing-file":
        env["BACKUP_SECRETS_SOURCE_FILE"] = str(root / "missing")
    elif failure == "missing-directory":
        env["BACKUP_SECRETS_SOURCE_DIR"] = str(root / "missing")
    elif failure == "file-is-directory":
        env["BACKUP_SECRETS_SOURCE_FILE"] = str(h["directory"])
    elif failure == "directory-is-file":
        env["BACKUP_SECRETS_SOURCE_DIR"] = str(h["file"])
    elif failure in {"file-link", "directory-link"}:
        target = h["file"] if failure == "file-link" else h["directory"]
        link = root / "link"
        link.symlink_to(target)
        key = "BACKUP_SECRETS_SOURCE_FILE" if failure == "file-link" else "BACKUP_SECRETS_SOURCE_DIR"
        env[key] = str(link)
    else:
        link = root / "parent-link"
        link.symlink_to(root, target_is_directory=True)
        suffix = h["file"].name if failure == "parent-link" else f"../{root.name}/{h['file'].name}"
        env["BACKUP_SECRETS_SOURCE_FILE"] = f"{link}/{suffix}"
    result = invoke(h, "--profile", "backup", "up", "-d", "backup-agent")
    assert result.returncode != 0
    assert not (root / "calls.jsonl").exists()
    assert not (root / "override.json").exists()
    assert not (root / "missing").exists()


def test_wrapper_never_evaluates_malicious_source_path(backup_harness: dict) -> None:
    h = backup_harness
    h["environment"]["BACKUP_SECRETS_SOURCE_FILE"] = "$(touch INJECTED)"
    assert invoke(h, "config").returncode != 0
    assert not (h["project"] / "INJECTED").exists()


def test_wrapper_uses_compose_environment_and_forwards_env_file_order(
    backup_harness: dict,
) -> None:
    h = backup_harness
    env = h["environment"]
    env.pop("BACKUP_SECRETS_SOURCE_FILE")
    env.pop("BACKUP_SECRETS_SOURCE_DIR")
    env["COMPOSE_TEST_ENVIRONMENT"] = (
        f"BACKUP_SECRETS_SOURCE_FILE={h['file']}\n"
        f"BACKUP_SECRETS_SOURCE_DIR={h['directory']}\n"
    )
    assert invoke(h, "--env-file", "first.env", "--env-file=second.env", "config").returncode == 0
    calls = json.loads((h["root"] / "calls.jsonl").read_text().splitlines()[0])
    assert calls[-4:] == ["--env-file", "first.env", "--env-file=second.env", "config"]


def test_wrapper_process_environment_wins_over_compose_values(backup_harness: dict) -> None:
    h = backup_harness
    h["environment"]["COMPOSE_TEST_ENVIRONMENT"] = "BACKUP_SECRETS_SOURCE_FILE=/missing\nBACKUP_SECRETS_SOURCE_DIR=/missing"
    assert invoke(h, "config").returncode == 0
    mounts = json.loads((h["root"] / "override.json").read_text())["services"]["backup-agent"]["volumes"]
    assert mounts[0]["source"] == str(h["file"]).replace("$", "$$")
    assert mounts[1]["source"] == str(h["directory"]).replace("$", "$$")


def test_wrapper_invalid_process_environment_does_not_fall_back_to_dotenv(
    backup_harness: dict,
) -> None:
    h = backup_harness
    h["environment"]["COMPOSE_TEST_ENVIRONMENT"] = (
        f"BACKUP_SECRETS_SOURCE_FILE={h['file']}\n"
        f"BACKUP_SECRETS_SOURCE_DIR={h['directory']}"
    )
    h["environment"]["BACKUP_SECRETS_SOURCE_FILE"] = "/missing"
    assert invoke(h, "config").returncode != 0
    assert not (h["root"] / "calls.jsonl").exists()
    assert not (h["root"] / "override.json").exists()


def test_wrapper_preserves_complete_standard_input_stream(backup_harness: dict) -> None:
    h = backup_harness
    h["environment"]["CAPTURE_STDIN"] = "1"
    payload = b"synthetic-restore-stream\n" * 16384
    result = subprocess.run(
        ["bash", str(h["script"]), "exec", "-T", "postgres", "pg_restore"],
        cwd=h["project"], env=h["environment"], input=payload,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (h["root"] / "stdin").read_bytes() == payload


@pytest.mark.parametrize("exit_code", [0, 73])
def test_wrapper_removes_temporary_override_after_compose_exit(
    backup_harness: dict, exit_code: int,
) -> None:
    h = backup_harness
    h["environment"]["DOCKER_EXIT"] = str(exit_code)
    assert invoke(h, "config").returncode == exit_code
    metadata = json.loads((h["root"] / "metadata.json").read_text())
    assert not Path(metadata["path"]).parent.exists()


@pytest.mark.parametrize("received_signal", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_wrapper_cleans_up_on_signals(backup_harness: dict, received_signal: int) -> None:
    h = backup_harness
    h["environment"]["DOCKER_WAIT"] = "1"
    process = subprocess.Popen(["bash", str(h["script"]), "config"], cwd=h["project"], env=h["environment"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not (h["root"] / "ready").exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        process.send_signal(received_signal)
        process.communicate(timeout=5)
        assert process.returncode != 0
        metadata = json.loads((h["root"] / "metadata.json").read_text())
        assert not Path(metadata["path"]).parent.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_wrapper_rejects_additional_compose_definitions(backup_harness: dict) -> None:
    assert invoke(backup_harness, "-f", "attacker.yml", "config").returncode != 0


@pytest.mark.parametrize("invalid", ["missing", "writable", "wrong-target", "duplicate", "not-bind"])
def test_restore_refuses_secret_mode_without_exact_readonly_sources(
    backup_harness: dict, invalid: str,
) -> None:
    h = backup_harness
    mounts = [
        {"Type": "bind", "Source": str(h["file"]), "Destination": "/backup-input/environment.env", "RW": False},
        {"Type": "bind", "Source": str(h["directory"]), "Destination": "/backup-input/secrets", "RW": False},
    ]
    if invalid == "missing":
        mounts.pop()
    elif invalid == "writable":
        mounts[0]["RW"] = True
    elif invalid == "wrong-target":
        mounts[0]["Destination"] = "/backup-input/other"
    elif invalid == "duplicate":
        mounts.append(mounts[0])
    else:
        mounts[0]["Type"] = "volume"
    h["environment"]["INSPECT_JSON"] = json.dumps([{
        "State": {"Running": True},
        "Config": {"Env": [
            "BACKUP_INCLUDE_SECRETS=true",
            "SECRETS_SOURCE_FILE=/backup-input/environment.env",
            "SECRETS_SOURCE_DIR=/backup-input/secrets",
        ]},
        "Mounts": mounts,
    }])
    backup = h["root"] / "synthetic.dump"
    backup.write_bytes(b"synthetic")
    h["environment"]["CONFIRM_RESTORE"] = "calograph"
    result = subprocess.run(["bash", str(h["project"] / "scripts/restore-postgres.sh"), str(backup)], cwd=h["project"], env=h["environment"], capture_output=True, text=True, check=False)
    assert result.returncode != 0
    calls = (h["root"] / "calls.jsonl").read_text()
    assert '"stop"' not in calls
    assert '"up"' not in calls


def test_restore_preserves_inspected_secret_sources_instead_of_current_environment(
    backup_harness: dict,
) -> None:
    h = backup_harness
    h["environment"]["INSPECT_JSON"] = json.dumps([{
        "State": {"Running": True},
        "Config": {"Env": [
            "BACKUP_INCLUDE_SECRETS=true",
            "SECRETS_SOURCE_FILE=/backup-input/environment.env",
            "SECRETS_SOURCE_DIR=/backup-input/secrets",
        ]},
        "Mounts": [
            {"Type": "bind", "Source": str(h["file"]), "Destination": "/backup-input/environment.env", "RW": False},
            {"Type": "bind", "Source": str(h["directory"]), "Destination": "/backup-input/secrets", "RW": False},
        ],
    }])
    h["environment"]["BACKUP_SECRETS_SOURCE_FILE"] = "/missing-current-environment"
    h["environment"]["BACKUP_SECRETS_SOURCE_DIR"] = "/missing-current-environment"
    h["environment"]["CONFIRM_RESTORE"] = "calograph"
    backup = h["root"] / "synthetic.dump"
    backup.write_bytes(b"synthetic")
    result = subprocess.run(
        ["bash", str(h["project"] / "scripts/restore-postgres.sh"), str(backup)],
        cwd=h["project"], env=h["environment"], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    mounts = json.loads((h["root"] / "override.json").read_text())["services"]["backup-agent"]["volumes"]
    assert mounts[0]["source"] == str(h["file"]).replace("$", "$$")
    assert mounts[1]["source"] == str(h["directory"]).replace("$", "$$")
