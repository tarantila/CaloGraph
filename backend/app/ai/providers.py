"""Provider interfaces live in :mod:`app.ai.contracts`.

Concrete adapters belong in separate modules and must be dependency-injected by
their caller. This module intentionally performs no provider registration or I/O.
"""

from app.ai.contracts import AIProvider

__all__ = ["AIProvider"]
