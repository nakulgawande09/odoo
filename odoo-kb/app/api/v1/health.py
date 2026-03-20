"""Health check endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import get_backend

router = APIRouter()


@router.get("/health")
async def health_check(backend=Depends(get_backend)) -> dict:
    """Check service and backend health."""
    backend_healthy = await backend.health_check()
    return {
        "status": "healthy" if backend_healthy else "degraded",
        "backends": {
            backend.name: backend_healthy,
        },
    }
