import pytest

from app.services.import_guard import ImportAlreadyRunning, import_slot


def test_only_one_import_slot_per_user() -> None:
    with (
        import_slot("user-id"),
        pytest.raises(ImportAlreadyRunning),
        import_slot("user-id"),
    ):
        pass

    with import_slot("user-id"):
        pass
