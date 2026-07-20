import hashlib
import hmac
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RateLimitBucket


def check_rate_limit(db: Session, action: str, key: str, limit: int) -> None:
    now = datetime.now(UTC)
    window = now.replace(second=0, microsecond=0)
    key_hash = hmac.new(
        settings.rate_limit_secret.encode(), key.encode(), hashlib.sha256
    ).hexdigest()
    bucket = db.scalar(
        select(RateLimitBucket).where(
            RateLimitBucket.key_hash == key_hash,
            RateLimitBucket.action == action,
            RateLimitBucket.window_start == window,
        )
    )
    if bucket and bucket.count >= limit:
        raise HTTPException(
            status_code=429, detail="Zu viele Anfragen. Bitte später erneut versuchen."
        )
    if bucket:
        bucket.count += 1
    else:
        db.add(RateLimitBucket(key_hash=key_hash, action=action, window_start=window, count=1))
    db.commit()
