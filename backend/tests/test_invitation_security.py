from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.auth.security import create_registration_state, verify_registration_state


def test_registration_state_is_signed_and_expires_after_ten_minutes() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    invitation_id = uuid4()
    state = create_registration_state(invitation_id, now)

    assert verify_registration_state(state, now + timedelta(minutes=9)) == invitation_id
    assert verify_registration_state(f"{state}tampered", now) is None
    assert verify_registration_state(state, now + timedelta(minutes=10)) is None
