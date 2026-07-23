from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class CredentialEncryptionError(RuntimeError):
    pass


def generate_credential_key() -> str:
    return Fernet.generate_key().decode()


def encrypt_credential(value: str) -> bytes:
    if not value:
        raise CredentialEncryptionError("Leere Zugangsdaten können nicht gespeichert werden.")
    return _cipher().encrypt(value.encode())


def decrypt_credential(value: bytes) -> str:
    try:
        return _cipher().decrypt(value).decode()
    except (InvalidToken, UnicodeDecodeError) as exc:
        raise CredentialEncryptionError(
            "Gespeicherte Zugangsdaten konnten nicht entschlüsselt werden."
        ) from exc


def _cipher() -> Fernet:
    if not settings.credential_encryption_key:
        raise CredentialEncryptionError(
            "CREDENTIAL_ENCRYPTION_KEY ist nicht konfiguriert."
        )
    return Fernet(settings.credential_encryption_key.encode())
