"""Inspect the resolved Compose contract without starting containers or reading .env."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def repository_root() -> Path:
    for root in (Path(__file__).resolve().parents[2], Path('/workspace')):
        if (root / 'docker-compose-dev.yml').exists():
            return root
    raise AssertionError('standalone development Compose definition is missing')


def compose_config(*profiles: str) -> dict:
    if not shutil.which('docker'):
        if os.environ.get('CALOGRAPH_REQUIRE_COMPOSE_TESTS') == '1':
            pytest.fail('Docker Compose must be available for CI contract coverage')
        pytest.skip('Docker Compose is required for resolved configuration tests')
    root = repository_root()
    command = ['docker', 'compose', '--env-file', '/dev/null',
               '-f', str(root / 'docker-compose-dev.yml')]
    for profile in profiles:
        command.extend(['--profile', profile])
    environment = {
        key: value for key, value in os.environ.items()
        if key in {'PATH', 'HOME', 'DOCKER_CONFIG', 'DOCKER_HOST'}
    }
    environment.update({
        'ENVIRONMENT': 'test',
        'CALOGRAPH_PUBLIC_URL': 'http://localhost:8180',
        'COOKIE_SECURE': 'false',
        'ENABLE_HSTS': 'false',
        'TRUSTED_HOSTS': 'localhost,127.0.0.1',
        'TRUSTED_ORIGINS': 'http://localhost:8180,http://127.0.0.1:8180',
    })
    result = subprocess.run([*command, 'config', '--format', 'json'], check=True,
                            capture_output=True, text=True,
                            env=environment)
    return json.loads(result.stdout)


def test_default_development_builds_local_images_and_scheduler_waits_for_backend() -> None:
    config = compose_config('*')
    assert config['name'] == 'calograph-dev'
    services = {
        name: service for name, service in config['services'].items()
        if not service.get('profiles')
    }
    assert set(services) == {'postgres', 'backend', 'frontend', 'yazio-scheduler'}
    backend, frontend, scheduler = (services[name] for name in ('backend', 'frontend', 'yazio-scheduler'))
    assert backend['build']['target'] == 'backend-development'
    assert frontend['build']['target'] == 'frontend-development'
    assert backend['image'] == scheduler['image'] == 'calograph-backend-dev:edge'
    assert frontend['image'] == 'calograph-frontend-dev:edge'
    assert scheduler['pull_policy'] == 'never'
    assert 'build' not in scheduler
    assert scheduler['depends_on']['backend']['condition'] == 'service_healthy'
    assert frontend['ports'][0]['target'] == 5173


@pytest.mark.parametrize('profile,prefix', [('e2e', 'e2e'), ('verification', 'verification')])
def test_browser_graphs_use_runtime_images_synthetic_tmpfs_database_and_no_dev_dependencies(profile, prefix) -> None:
    services = compose_config(profile)['services']
    names = {f'{prefix}-postgres', f'{prefix}-backend', f'{prefix}-frontend', prefix}
    for name in names:
        assert set(services[name].get('depends_on', {})) <= names
    assert services[f'{prefix}-backend']['build']['target'] == 'backend-runtime'
    assert services[f'{prefix}-frontend']['build']['target'] == 'frontend-runtime'
    database = services[f'{prefix}-postgres']
    assert database['tmpfs'] == ['/var/lib/postgresql']
    assert 'backend' in services[f'{prefix}-backend']['networks'][f'{prefix}-edge']['aliases']
    assert 'frontend' in services[f'{prefix}-frontend']['networks'][f'{prefix}-edge']['aliases']
    assert not database.get('volumes')
    assert services[prefix]['environment']['BASE_URL'] == 'http://frontend:8080'
    assert services[f'{prefix}-backend']['environment']['YAZIO_SCHEDULER_ENABLED'] == 'false'


def test_analysis_acceptance_shares_only_the_isolated_frontend_loopback() -> None:
    services = compose_config('e2e')['services']
    acceptance = services['analysis-acceptance']
    backend = services['e2e-backend']['environment']
    assert acceptance['network_mode'] == 'service:e2e-frontend'
    assert acceptance['environment']['BASE_URL'] == 'http://localhost:8080'
    assert 'http://localhost:8080' in backend['TRUSTED_ORIGINS']
    assert 'http://127.0.0.1:8080' in backend['TRUSTED_ORIGINS']
    assert acceptance['command'][-2:] == ['--project=chromium', '--workers=1']


def test_review_retains_owned_volume_and_does_not_require_review_variables_for_other_profiles() -> None:
    config = compose_config('analysis-review')
    services = config['services']
    assert services['review-postgres']['environment']['POSTGRES_DB'] == 'calograph_analysis_v2_test'
    assert services['review-postgres']['volumes'][0]['source'] == 'postgres-data'
    assert config['volumes']['postgres-data']['name'] == 'calograph-analysis-v2-review-postgres-data'
    frontend = services['review-frontend']
    assert frontend['ports'][0]['host_ip'] == '127.0.0.1'
    assert frontend['ports'][0]['published'] == '8289'
    assert services['review-backend']['environment']['RUN_MIGRATIONS'] == 'false'
    for secret in services['review-backend']['secrets']:
        assert secret['target'] == secret['source'].removeprefix('review-')
