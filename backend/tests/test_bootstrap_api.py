from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import verify_password
from app.config import settings
from app.models import TrackingQualitySettings, User, UserOnboarding


def test_bootstrap_is_fail_closed_and_creates_invariants(client: TestClient, db, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'initial_admin_setup_enabled', False)
    assert client.get('/api/v1/auth/bootstrap/status').json() == {'setup_required': False}
    monkeypatch.setattr(settings, 'initial_admin_setup_enabled', True)
    assert client.get('/api/v1/auth/bootstrap/status').json() == {'setup_required': True}

    response = client.post(
        '/api/v1/auth/bootstrap',
        json={'username': 'first-admin', 'password': 'correct-horse-battery-staple'},
    )
    assert response.status_code == 201
    created = db.scalar(select(User).where(User.username == 'first-admin'))
    assert created is not None and created.is_admin and created.is_active
    assert verify_password(created.password_hash, 'correct-horse-battery-staple')
    assert db.get(UserOnboarding, created.id) is not None
    assert db.get(TrackingQualitySettings, created.id) is not None
    assert client.get('/api/v1/auth/bootstrap/status').json() == {'setup_required': False}


def test_bootstrap_is_unavailable_when_disabled(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'initial_admin_setup_enabled', False)
    response = client.post(
        '/api/v1/auth/bootstrap',
        json={'username': 'first-admin', 'password': 'correct-horse-battery-staple'},
    )
    assert response.status_code == 404


def test_concurrent_bootstrap_has_one_winner(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, 'initial_admin_setup_enabled', True)

    def attempt(index: int) -> int:
        with TestClient(client.app) as concurrent_client:
            return concurrent_client.post(
                '/api/v1/auth/bootstrap',
                json={
                    'username': f'admin-{index}',
                    'password': 'correct-horse-battery-staple',
                },
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(attempt, range(2)))
    assert sorted(statuses) == [201, 409]
