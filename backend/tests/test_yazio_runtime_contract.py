"""Guard the shipped SDK-only dependency and runtime attribution contract."""

import tomllib
from pathlib import Path


def test_runtime_dependency_and_license_contract_is_sdk_only() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    repository_root = next(
        root
        for root in (backend_root.parent, Path("/workspace"))
        if (root / "Dockerfile").exists()
    )
    project = tomllib.loads((backend_root / "pyproject.toml").read_text())
    lock = tomllib.loads((backend_root / "uv.lock").read_text())
    dependencies = project["project"]["dependencies"]
    locked_packages = {package["name"]: package for package in lock["package"]}

    assert "yazio-sdk==0.4.0" in dependencies
    assert not any(dependency.startswith("yazio-exporter") for dependency in dependencies)
    assert "yazio-exporter" not in locked_packages
    assert locked_packages["yazio-sdk"]["version"] == "0.4.0"
    backend_dependencies = {
        dependency["name"]
        for dependency in locked_packages["calograph-backend"]["dependencies"]
    }
    assert "yazio-sdk" in backend_dependencies
    assert "yazio-exporter" not in backend_dependencies

    dockerfile = (repository_root / "Dockerfile").read_text()
    notices = (repository_root / "THIRD_PARTY_NOTICES.md").read_text()
    license_directory = repository_root / "THIRD_PARTY_LICENSES"
    sdk_license = license_directory / "yazio-sdk-MIT.txt"
    assert sdk_license.is_file()
    assert "MIT License" in sdk_license.read_text()
    assert not (license_directory / "yazio-exporter-MIT.txt").exists()
    assert "yazio-exporter" not in dockerfile
    assert "yazio-exporter" not in notices
    assert "THIRD_PARTY_LICENSES/yazio-sdk-MIT.txt /licenses/yazio-sdk-MIT.txt" in dockerfile
    assert "test -r /licenses/yazio-sdk-MIT.txt" in dockerfile
    assert "yazio-sdk==0.4.0" in notices
    assert "THIRD_PARTY_LICENSES/yazio-sdk-MIT.txt" in notices

    # Keep the generic harvested runtime inventory, not just the explicit SDK license.
    assert "find /opt/venv -type f" in dockerfile
    assert "COPY --from=backend-base /third-party-licenses/backend /licenses/backend" in dockerfile
    assert 'find /licenses/backend -type f -print -quit' in dockerfile
    assert "/licenses/backend" in notices
    assert "/licenses/frontend" in notices
