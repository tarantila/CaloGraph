from fastapi import APIRouter

from app.api import (
    achievements,
    admin,
    ai_checks,
    ai_connections,
    analytics,
    auth,
    expert,
    google_health,
    imports,
    settings,
    source_priority,
    users,
    withings,
    yazio,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(achievements.router)
api_router.include_router(auth.router)
api_router.include_router(ai_connections.router)
api_router.include_router(ai_checks.router)
api_router.include_router(expert.router)
api_router.include_router(imports.router)
api_router.include_router(analytics.router)
api_router.include_router(settings.router)
api_router.include_router(yazio.router)
api_router.include_router(admin.router)
api_router.include_router(users.router)
api_router.include_router(google_health.router)
api_router.include_router(source_priority.router)
api_router.include_router(withings.router)
