"""Aggregate all v1 API routers."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import analytics, calls, crm, documents, health, ingest, messaging, search, voip, vonage

router = APIRouter(prefix="/v1")
router.include_router(search.router, tags=["search"])
router.include_router(ingest.router, tags=["ingest"])
router.include_router(documents.router, tags=["documents"])
router.include_router(health.router, tags=["health"])
router.include_router(analytics.router, tags=["analytics"])
router.include_router(voip.router, tags=["voip"])
router.include_router(vonage.router, tags=["vonage"])
router.include_router(calls.router, tags=["calls"])
router.include_router(messaging.router, tags=["messaging"])
router.include_router(crm.router, tags=["crm"])
