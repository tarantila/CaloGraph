from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class MfaEncryptionError(RuntimeError):
    pass


def encrypt_mfa_secret(value: str) -> bytes:
    if not value:
        raise MfaEncryptionError("Leeres MFA-Geheimnis kann nicht gespeichert werden.")
    return _cipher().encrypt(value.encode())


def decrypt_mfa_secret(value: bytes) -> str:
    try:
        return _cipher().decrypt(value).decode()
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise MfaEncryptionError(
            "Gespeichertes MFA-Geheimnis konnte nicht entschlüsselt werden."
        ) from exc


def _cipher() -> Fernet:
    if not settings.mfa_encryption_key:
        raise MfaEncryptionError("MFA_ENCRYPTION_KEY ist nicht konfiguriert.")
    return Fernet(settings.mfa_encryption_key.encode())
