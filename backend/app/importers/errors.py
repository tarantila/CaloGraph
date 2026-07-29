class ImportFormatError(ValueError):
    """A safe, client-facing error for an unusable import payload."""


class ImportFieldError(ImportFormatError):
    """A safe error for one malformed sample field."""


class ImportLimitError(ImportFormatError):
    """A configured import record or materialization limit was exceeded."""


def safe_sample_error(exc: Exception) -> str:
    if isinstance(exc, ImportFieldError):
        return str(exc)
    return "Messwert enthält ungültige oder zu lange Felder"
