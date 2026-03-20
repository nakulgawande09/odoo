"""Aggregate all v1 API routers."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import analytics, documents, health, ingest, search

router = APIRouter(prefix="/v1")
router.include_router(search.router, tags=["search"])
router.include_router(ingest.router, tags=["ingest"])
router.include_router(documents.router, tags=["documents"])
router.include_router(health.router, tags=["health"])
router.include_router(analytics.router, tags=["analytics"])
