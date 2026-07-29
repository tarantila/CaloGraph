import hashlib
import unicodedata
from functools import lru_cache
from pathlib import Path

MIN_PASSWORD_LENGTH = 15
MAX_PASSWORD_LENGTH = 1_024
PASSWORD_BLOCKLIST_PATH = Path(__file__).with_name("common_passwords.sha256")
SHA256_DIGEST_BYTES = 32


class PasswordPolicyError(ValueError):
    pass


def _normalized_password(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


@lru_cache(maxsize=1)
def _blocked_password_digests() -> frozenset[bytes]:
    raw = PASSWORD_BLOCKLIST_PATH.read_bytes()
    if not raw or len(raw) % SHA256_DIGEST_BYTES:
        raise RuntimeError("The packaged password blocklist is missing or malformed")
    return frozenset(
        raw[offset : offset + SHA256_DIGEST_BYTES]
        for offset in range(0, len(raw), SHA256_DIGEST_BYTES)
    )


def validate_password_blocklist() -> None:
    if len(_blocked_password_digests()) < 10_000:
        raise RuntimeError("The packaged password blocklist is unexpectedly small")


def validate_new_password(password: str, username: str | None = None) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Das Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen lang sein."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Das Passwort darf höchstens {MAX_PASSWORD_LENGTH} Zeichen lang sein."
        )

    normalized = _normalized_password(password)
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    if digest in _blocked_password_digests():
        raise PasswordPolicyError(
            "Dieses Passwort ist häufig verwendet oder aus Datenlecks bekannt. "
            "Bitte wähle ein anderes Passwort."
        )

    contextual_values = {"calograph", "calograph123", "calograph-password"}
    if username:
        normalized_username = _normalized_password(username)
        contextual_values.update(
            {
                normalized_username,
                f"{normalized_username}123",
                f"{normalized_username}1234",
                f"{normalized_username}password",
                f"{normalized_username}-password",
            }
        )
    if normalized in contextual_values:
        raise PasswordPolicyError(
            "Das Passwort darf nicht aus dem Benutzernamen oder App-Namen bestehen."
        )
