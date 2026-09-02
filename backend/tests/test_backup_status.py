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
        'automation': {'enabled': True, 'last_success_at': now.isoformat(), 'last_error_code': 'verification_failed'},
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
        'schema_version': 1, 'target': 'calograph', 'result': 'ARCHIVE_VERIFIED',
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
    assert payload['recovery']['archive_verification']['components']['database']['state'] == 'verified'
    assert payload['recovery']['archive_verification']['components']['database']['latest_artifact_verified'] is True
    assert payload['recovery']['restore_test']['state'] == 'unknown'
    assert 'artifact' not in json.dumps(payload)
    assert 'sha256' not in json.dumps(payload)

    (tmp_path / 'database-verification.json').write_text(json.dumps({
        'schema_version': 1, 'target': 'calograph', 'result': 'ARCHIVE_VERIFIED',
        'component': 'database', 'artifact': 'other.dump.age', 'sha256': checksum,
        'verified_at': now.isoformat(),
    }))
    payload = client.get('/api/v1/admin/backup-status').json()
    assert payload['overall_state'] == 'healthy'
    assert payload['recovery']['archive_verification']['components']['database']['state'] == 'not_verified'
    assert payload['recovery']['archive_verification']['components']['database']['latest_artifact_verified'] is False
    assert payload['recovery']['archive_verification']['components']['database']['verified_at'] == now.isoformat().replace('+00:00', 'Z')

def test_restore_test_states_are_independent_and_sanitized(client, user, db, tmp_path, monkeypatch) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    report = {
        'schema_version': 1, 'reported_at': now.isoformat(), 'target': 'calograph',
        'automation': {'enabled': True, 'last_success_at': now.isoformat()},
        'components': {
            'database': {'state': 'healthy', 'matching_backup': True, 'last_success_at': now.isoformat()},
            'environment_secrets': {'state': 'disabled'},
        },
    }
    status_file = tmp_path / 'status.json'
    restore_file = tmp_path / 'restore-test.json'
    status_file.write_text(json.dumps(report))
    monkeypatch.setattr(settings, 'backup_agent_enabled', True)
    monkeypatch.setattr(settings, 'backup_status_file', str(status_file))
    monkeypatch.setattr(settings, 'backup_restore_test_status_file', str(restore_file))
    user.is_admin = True
    db.commit()
    _login(client)

    restore_file.write_text(json.dumps({
        'schema_version': 1, 'result': 'RESTORE_TESTED',
        'tested_at': now.isoformat(), 'artifact': 'calograph-test.dump.age', 'sha256': 'b' * 64,
    }))
    payload = client.get('/api/v1/admin/backup-status').json()
    assert payload['overall_state'] == 'healthy'
    assert payload['recovery']['restore_test']['state'] == 'current'
    assert 'calograph-test.dump.age' not in json.dumps(payload)
    assert 'b' * 64 not in json.dumps(payload)

    restore_file.write_text(json.dumps({
        'schema_version': 1, 'result': 'NEVER_TESTED', 'tested_at': now.isoformat(),
    }))
    payload = client.get('/api/v1/admin/backup-status').json()
    assert payload['recovery']['restore_test']['state'] == 'never_tested'
    assert payload['recovery']['restore_test']['result'] == 'NEVER_TESTED'

    due_at = (now - timedelta(days=91)).isoformat()
    restore_file.write_text(json.dumps({
        'schema_version': 1, 'result': 'RESTORE_TESTED',
        'tested_at': due_at, 'artifact': 'calograph-old.dump.age', 'sha256': 'c' * 64,
    }))
    payload = client.get('/api/v1/admin/backup-status').json()
    assert payload['recovery']['restore_test']['state'] == 'due'
    assert payload['recovery']['restore_test']['last_success_at'] == due_at.replace('+00:00', 'Z')

    previous_success = (now - timedelta(days=91)).isoformat().replace('+00:00', 'Z')
    restore_file.write_text(json.dumps({
        'schema_version': 1, 'result': 'RESTORE_TEST_FAILED',
        'tested_at': now.isoformat(), 'artifact': 'calograph-test.dump.age', 'sha256': 'b' * 64,
        'failure_code': 'schema_check_failed', 'last_success_at': previous_success,
        'last_success_artifact': 'calograph-old.dump.age', 'last_success_sha256': 'c' * 64,
    }))
    payload = client.get('/api/v1/admin/backup-status').json()
    assert payload['recovery']['restore_test']['state'] == 'failed'
    assert payload['recovery']['restore_test']['last_success_at'] == previous_success

    restore_file.write_text(json.dumps({
        'schema_version': 1, 'result': 'RESTORE_TEST_FAILED',
        'tested_at': now.isoformat(), 'artifact': '../unsafe.dump.age', 'sha256': 'b' * 64,
        'failure_code': 'not-an-allow-listed-code',
    }))
    assert client.get('/api/v1/admin/backup-status').json()['recovery']['restore_test']['state'] == 'unknown'

    restore_file.write_text(json.dumps({
        'schema_version': 1, 'result': 'RESTORE_TESTED',
        'tested_at': (now + timedelta(days=1)).isoformat(),
        'artifact': 'calograph-test.dump.age', 'sha256': 'b' * 64,
    }))
    assert client.get('/api/v1/admin/backup-status').json()['recovery']['restore_test']['state'] == 'unknown'

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
