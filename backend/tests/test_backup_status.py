import json
from datetime import UTC, datetime, timedelta

from app.config import settings

def _login(client, username: str = 'admin') -> None:
    response = client.post('/api/v1/auth/login', json={'username': username, 'password': 'correct-horse-battery-staple'})
    assert response.status_code == 200


def test_backup_status_is_admin_only_and_disabled_by_default(client, user) -> None:
    assert client.get('/api/v1/admin/backup-status').status_code == 401
    _login(client)
    response = client.get('/api/v1/admin/backup-status')
    assert response.status_code == 200
    assert response.json()['overall_state'] == 'disabled'


def test_backup_status_sanitizes_report_and_aggregates_health(client, user, tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    report = {
        'schema_version': 1,
        'reported_at': now.isoformat(),
        'target': 'calograph',
        'freshness_threshold_seconds': 172800,
        'automation': {'enabled': True, 'last_success_at': now.isoformat()},
        'components': {
            'database': {'state': 'healthy', 'verification': 'full', 'encryption': 'age'},
            'environment_secrets': {'state': 'healthy', 'verification': 'full', 'encryption': 'age'},
        },
        'private_path': '/should/not/escape',
        'secret_value': 'should-not-appear',
    }
    status_file = tmp_path / 'status.json'
    status_file.write_text(json.dumps(report))
    monkeypatch.setattr(settings, 'backup_agent_enabled', True)
    monkeypatch.setattr(settings, 'backup_status_file', str(status_file))
    _login(client)
    response = client.get('/api/v1/admin/backup-status')
    assert response.status_code == 200
    payload = response.json()
    assert payload['overall_state'] == 'healthy'
    assert 'private_path' not in payload and 'secret_value' not in response.text


def test_backup_status_expired_report_is_unknown(client, user, tmp_path, monkeypatch) -> None:
    old = datetime.now(UTC) - timedelta(days=10)
    status_file = tmp_path / 'status.json'
    status_file.write_text(json.dumps({'schema_version': 1, 'reported_at': old.isoformat(), 'target': 'calograph', 'components': {}}))
    monkeypatch.setattr(settings, 'backup_agent_enabled', True)
    monkeypatch.setattr(settings, 'backup_status_file', str(status_file))
    _login(client)
    payload = client.get('/api/v1/admin/backup-status').json()
    assert payload['overall_state'] == 'unknown'
    assert payload['reason_codes'] == ['report_expired']
