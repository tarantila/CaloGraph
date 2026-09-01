import json
from datetime import UTC, datetime, timedelta

from app.config import settings


def _login(client, username: str = 'admin') -> None:
    response = client.post('/api/v1/auth/login', json={'username': username, 'password': 'correct-horse-battery-staple'})
    assert response.status_code == 200


def test_backup_status_is_admin_only_and_disabled_by_default(client, user, db) -> None:
    assert client.get('/api/v1/admin/backup-status').status_code == 401
    user.is_admin = True
    db.commit()
    _login(client)
    response = client.get('/api/v1/admin/backup-status')
    assert response.status_code == 200
    assert response.json()['overall_state'] == 'disabled'


def test_backup_status_sanitizes_report_and_aggregates_health(client, user, db, tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    report = {
        'schema_version': 1,
        'reported_at': now.isoformat(),
        'target': 'calograph',
        'freshness_threshold_seconds': 172800,
        'automation': {'enabled': True, 'last_success_at': now.isoformat()},
        'components': {
            'database': {'state': 'healthy', 'verification': 'full', 'encryption': 'age', 'matching_backup': True, 'last_success_at': now.isoformat()},
            'environment_secrets': {'state': 'healthy', 'verification': 'full', 'encryption': 'age', 'matching_backup': True, 'last_success_at': now.isoformat()},
        },
        'private_path': '/should/not/escape',
        'secret_value': 'should-not-appear',
    }
    status_file = tmp_path / 'status.json'
    status_file.write_text(json.dumps(report))
    monkeypatch.setattr(settings, 'backup_agent_enabled', True)
    monkeypatch.setattr(settings, 'backup_status_file', str(status_file))
    user.is_admin = True
    db.commit()

    _login(client)
    response = client.get('/api/v1/admin/backup-status')
    assert response.status_code == 200
    payload = response.json()
    assert payload['overall_state'] == 'healthy'
    serialized = json.dumps(payload)
    assert 'should-not-appear' not in serialized
    assert '/should/not/escape' not in serialized


def test_backup_status_requires_matching_external_verification_record(client, user, db, tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    artifact = 'calograph-20260901T080000Z.dump.age'
    checksum = 'a' * 64
    report = {
        'schema_version': 1,
        'reported_at': now.isoformat(),
        'target': 'calograph',
        'freshness_threshold_seconds': 172800,
        'automation': {'enabled': True, 'last_success_at': now.isoformat()},
        'components': {
            'database': {
                'state': 'healthy', 'verification': 'not_verified', 'encryption': 'age',
                'matching_backup': True, 'artifact': artifact, 'sha256': checksum,
                'last_success_at': now.isoformat(),
            },
            'environment_secrets': {'state': 'disabled', 'verification': 'not_reported'},
        },
    }
    status_file = tmp_path / 'status.json'
    status_file.write_text(json.dumps(report))
    (tmp_path / 'database-verification.json').write_text(json.dumps({
        'schema_version': 1, 'target': 'calograph', 'result': 'RESTORE_VERIFIED',
        'component': 'database', 'artifact': artifact, 'sha256': checksum,
        'verified_at': now.isoformat(),
    }))
    monkeypatch.setattr(settings, 'backup_agent_enabled', True)
    monkeypatch.setattr(settings, 'backup_status_file', str(status_file))
    monkeypatch.setattr(settings, 'backup_database_verification_status_file', str(tmp_path / 'database-verification.json'))
    monkeypatch.setattr(settings, 'backup_secrets_verification_status_file', str(tmp_path / 'secrets-verification.json'))
    user.is_admin = True
    db.commit()

    _login(client)
    payload = client.get('/api/v1/admin/backup-status').json()
    assert payload['overall_state'] == 'healthy'
    assert payload['components']['database']['verification'] == 'full'
    assert payload['components']['database']['last_verified_at'] == now.isoformat().replace('+00:00', 'Z')
    assert 'artifact' not in json.dumps(payload)
    assert 'sha256' not in json.dumps(payload)

    (tmp_path / 'database-verification.json').write_text(json.dumps({
        'schema_version': 1, 'target': 'calograph', 'result': 'RESTORE_VERIFIED',
        'component': 'database', 'artifact': 'other.dump.age', 'sha256': checksum,
        'verified_at': now.isoformat(),
    }))
    payload = client.get('/api/v1/admin/backup-status').json()
    assert payload['overall_state'] == 'attention'
    assert 'verification_missing' in payload['reason_codes']

def test_backup_status_does_not_require_disabled_optional_secrets(client, user, db, tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC)
    report = {
        'schema_version': 1,
        'reported_at': now.isoformat(),
        'freshness_threshold_seconds': 172800,
        'automation': {'enabled': True, 'last_success_at': now.isoformat()},
        'components': {
            'database': {'state': 'healthy', 'verification': 'full', 'encryption': 'age', 'matching_backup': True, 'last_success_at': now.isoformat()},
            'environment_secrets': {'state': 'disabled', 'verification': 'not_reported'},
        },
    }
    status_file = tmp_path / 'status.json'
    status_file.write_text(json.dumps(report))
    monkeypatch.setattr(settings, 'backup_agent_enabled', True)
    monkeypatch.setattr(settings, 'backup_status_file', str(status_file))
    user.is_admin = True
    db.commit()

    _login(client)
    assert client.get('/api/v1/admin/backup-status').json()['overall_state'] == 'healthy'


def test_backup_status_expired_report_is_unknown(client, user, db, tmp_path, monkeypatch) -> None:
    old = datetime.now(UTC) - timedelta(days=10)
    status_file = tmp_path / 'status.json'
    status_file.write_text(json.dumps({'schema_version': 1, 'reported_at': old.isoformat(), 'target': 'calograph', 'automation': {'enabled': True}, 'components': {}}))
    monkeypatch.setattr(settings, 'backup_agent_enabled', True)
    monkeypatch.setattr(settings, 'backup_status_file', str(status_file))
    user.is_admin = True
    db.commit()

    _login(client)
    payload = client.get('/api/v1/admin/backup-status').json()
    assert payload['overall_state'] == 'unknown'
    assert payload['reason_codes'] == ['report_expired']
