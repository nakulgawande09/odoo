"""Odoo entity linker enrichment provider.

Resolves KB search results back to Odoo records by matching source_ref
fields (e.g., "product.product:42") and enriching results with
connected entity information. This enables the frontend to link
directly to the relevant Odoo record.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.schemas.search import ConnectedEntity, SearchResultItem

logger = logging.getLogger(__name__)

# Pattern for source_ref: "model.name:id"
SOURCE_REF_PATTERN = re.compile(r"^([\w.]+):(\d+)$")


class OdooEntityLinker:
    """Enrichment provider that links search results to Odoo records.

    Uses Odoo's JSON-RPC API to resolve entity references found in
    search result metadata/source_ref fields.
    """

    def __init__(self, settings: Any) -> None:
        self._odoo_url = getattr(settings, "odoo_url", "")
        self._odoo_db = getattr(settings, "odoo_db", "")
        self._odoo_user = getattr(settings, "odoo_user", "")
        self._odoo_password = getattr(settings, "odoo_password", "")
        self._uid: int | None = None
        self._enabled = bool(self._odoo_url and self._odoo_db)

    @property
    def name(self) -> str:
        return "odoo_entity_linker"

    async def enrich(
        self, query: str, results: list[SearchResultItem]
    ) -> list[SearchResultItem]:
        """Enrich results with Odoo entity links."""
        if not self._enabled:
            return results

        # Collect all source_refs that need resolution
        refs_to_resolve: dict[str, list[int]] = {}  # model -> [ids]
        ref_map: dict[str, tuple[str, int]] = {}  # "model:id" -> (model, id)

        for item in results:
            ref = item.metadata.get("source_ref") or getattr(item, "source_ref", None)
            if not ref:
                # Check metadata for odoo_model and odoo_id
                model = item.metadata.get("odoo_model")
                odoo_id = item.metadata.get("odoo_id")
                if model and odoo_id:
                    ref = f"{model}:{odoo_id}"

            if ref:
                match = SOURCE_REF_PATTERN.match(str(ref))
                if match:
                    model, record_id = match.group(1), int(match.group(2))
                    refs_to_resolve.setdefault(model, []).append(record_id)
                    ref_map[ref] = (model, record_id)

        if not refs_to_resolve:
            return results

        # Resolve entities from Odoo
        resolved = await self._resolve_entities(refs_to_resolve)

        # Attach connected entities to results
        enriched = []
        for item in results:
            ref = item.metadata.get("source_ref")
            if not ref:
                model = item.metadata.get("odoo_model")
                odoo_id = item.metadata.get("odoo_id")
                if model and odoo_id:
                    ref = f"{model}:{odoo_id}"

            if ref and ref in resolved:
                entity_info = resolved[ref]
                entity = ConnectedEntity(
                    entity_type=entity_info["type"],
                    entity_id=ref,
                    entity_name=entity_info["name"],
                    odoo_model=entity_info["model"],
                    odoo_id=entity_info["id"],
                )
                updated = item.model_copy(
                    update={
                        "connected_entities": item.connected_entities + [entity],
                    }
                )
                enriched.append(updated)
            else:
                enriched.append(item)

        return enriched

    async def _resolve_entities(
        self, refs: dict[str, list[int]]
    ) -> dict[str, dict[str, Any]]:
        """Resolve Odoo records via JSON-RPC."""
        resolved: dict[str, dict[str, Any]] = {}

        try:
            uid = await self._authenticate()
            if uid is None:
                return resolved

            async with httpx.AsyncClient(timeout=10) as client:
                for model, ids in refs.items():
                    try:
                        # Read name_get equivalent
                        response = await client.post(
                            f"{self._odoo_url}/jsonrpc",
                            json={
                                "jsonrpc": "2.0",
                                "method": "call",
                                "params": {
                                    "service": "object",
                                    "method": "execute_kw",
                                    "args": [
                                        self._odoo_db,
                                        uid,
                                        self._odoo_password,
                                        model,
                                        "read",
                                        [ids],
                                        {"fields": ["id", "display_name"]},
                                    ],
                                },
                            },
                        )
                        data = response.json()
                        records = data.get("result", [])

                        # Determine entity type from model name
                        entity_type = _model_to_entity_type(model)

                        for rec in records:
                            ref_key = f"{model}:{rec['id']}"
                            resolved[ref_key] = {
                                "type": entity_type,
                                "name": rec.get("display_name", ""),
                                "model": model,
                                "id": rec["id"],
                            }
                    except Exception as e:
                        logger.warning(
                            "Failed to resolve %s records: %s", model, e
                        )

        except Exception as e:
            logger.warning("Odoo entity resolution failed: %s", e)

        return resolved

    async def _authenticate(self) -> int | None:
        """Authenticate with Odoo and cache UID."""
        if self._uid is not None:
            return self._uid

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"{self._odoo_url}/jsonrpc",
                    json={
                        "jsonrpc": "2.0",
                        "method": "call",
                        "params": {
                            "service": "common",
                            "method": "authenticate",
                            "args": [
                                self._odoo_db,
                                self._odoo_user,
                                self._odoo_password,
                                {},
                            ],
                        },
                    },
                )
                data = response.json()
                self._uid = data.get("result")
                return self._uid
        except Exception as e:
            logger.warning("Odoo authentication failed: %s", e)
            return None


def _model_to_entity_type(model: str) -> str:
    """Map Odoo model names to human-readable entity types."""
    mapping = {
        "product.product": "product",
        "product.template": "product",
        "res.partner": "contact",
        "sale.order": "sale_order",
        "purchase.order": "purchase_order",
        "account.move": "invoice",
        "helpdesk.ticket": "ticket",
        "project.task": "task",
        "project.project": "project",
        "hr.employee": "employee",
        "stock.picking": "delivery",
        "crm.lead": "lead",
        "knowledge.article": "article",
    }
    return mapping.get(model, model.replace(".", "_"))
