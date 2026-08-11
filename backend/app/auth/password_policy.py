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


def _password_pattern_candidates(value: str) -> set[str]:
    compact = "".join(character for character in value if character.isalnum())
    return {compact, compact.strip("0123456789")}


def _has_repeated_pattern(value: str) -> bool:
    return any(
        len(candidate) >= 8
        and any(
            len(candidate) % unit_length == 0
            and candidate == candidate[:unit_length] * (len(candidate) // unit_length)
            for unit_length in range(1, len(candidate) // 2 + 1)
        )
        for candidate in _password_pattern_candidates(value)
    )


def _has_obvious_sequence(value: str) -> bool:
    sequences = (
        "0123456789",
        "9876543210",
        "abcdefghijklmnopqrstuvwxyz",
        "zyxwvutsrqponmlkjihgfedcba",
        "qwertyuiopasdfghjklzxcvbnm",
        "mnbvcxzlkjhgfdsaqpoiuytrewq",
    )
    return any(
        len(candidate) >= 8 and any(candidate in sequence * 2 for sequence in sequences)
        for candidate in _password_pattern_candidates(value)
    )


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

    if _has_repeated_pattern(normalized) or _has_obvious_sequence(normalized):
        raise PasswordPolicyError(
            "Dieses Passwort enthält ein leicht erratbares Wiederholungs- oder "
            "Sequenzmuster. Verwende eine ungewöhnliche lange Passphrase oder "
            "ein vom Passwortmanager erzeugtes Passwort."
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
