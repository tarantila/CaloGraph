from fastapi import APIRouter

from app.api import analytics, auth, imports, settings, users, yazio

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(imports.router)
api_router.include_router(analytics.router)
api_router.include_router(settings.router)
api_router.include_router(yazio.router)
api_router.include_router(users.router)
