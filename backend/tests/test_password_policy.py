import pytest

from app.auth.password_policy import (
    PasswordPolicyError,
    _blocked_password_digests,
    validate_new_password,
    validate_password_blocklist,
)


def test_packaged_password_blocklist_is_substantial() -> None:
    validate_password_blocklist()
    assert len(_blocked_password_digests()) == 10_898


@pytest.mark.parametrize(
    "password",
    [
        "short-password",
        "x" * 1_025,
        "123456789qwerty",
        "123456789QWERTY",
        "testtesttesttest1",
        "abcabcabcabcabc1",
        "passwordpassword1",
        "aaaaaaaaaaaaaaa1",
        "abcdefghijklmnop",
        "123456789012345",
        "111111111111111",
    ],
)
def test_new_password_policy_rejects_short_common_or_guessable_passwords(
    password: str,
) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_new_password(password)


def test_new_password_policy_rejects_context_specific_derivative() -> None:
    with pytest.raises(PasswordPolicyError, match="Benutzernamen"):
        validate_new_password("verylongusername123", "verylongusername")


def test_new_password_policy_accepts_long_passphrase() -> None:
    validate_new_password("four unusual words stay memorable", "owner")


def test_new_password_policy_accepts_password_manager_value() -> None:
    validate_new_password("vN7!Qp2#Lx9@Tm4$Rs8", "owner")
