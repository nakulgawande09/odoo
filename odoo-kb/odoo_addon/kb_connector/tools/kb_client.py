"""Thin HTTP client for calling the KB microservice from Odoo.

Follows the same pattern as Odoo's IAP tools (iap_jsonrpc).
"""
import logging

import requests

from odoo import _, exceptions

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10  # seconds


def _get_config(env):
    """Get KB service URL and API key from system parameters."""
    icp = env["ir.config_parameter"].sudo()
    url = icp.get_param("kb.service.url", "http://localhost:8100")
    api_key = icp.get_param("kb.service.api_key", "")
    return url.rstrip("/"), api_key


def _headers(api_key):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def kb_search(env, query, source="generic", limit=10, **kwargs):
    """Search the knowledge base.

    Args:
        env: Odoo environment.
        query: Natural language search query.
        source: Caller identifier ("livechat", "chatbot", "whatsapp", etc.)
        limit: Max results to return.

    Returns:
        dict with keys: results, total_count, parsed_intent, etc.
    """
    url, api_key = _get_config(env)
    payload = {"query": query, "source": source, "limit": limit, **kwargs}
    try:
        resp = requests.post(
            f"{url}/v1/search",
            json=payload,
            headers=_headers(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.Timeout:
        logger.warning("KB service timed out")
        raise exceptions.UserError(_("Knowledge Base service timed out."))
    except requests.exceptions.ConnectionError:
        logger.warning("KB service unavailable at %s", url)
        raise exceptions.UserError(_("Knowledge Base service is unavailable."))
    except requests.exceptions.HTTPError as e:
        logger.error("KB search error: %s", e)
        raise exceptions.UserError(_("Knowledge Base search failed: %s") % str(e))


def kb_ingest(env, content, title=None, content_type="text/plain",
              tags=None, source_ref=None, metadata=None):
    """Ingest a document into the knowledge base."""
    url, api_key = _get_config(env)
    payload = {
        "content": content,
        "title": title,
        "content_type": content_type,
        "tags": tags or [],
        "source_ref": source_ref,
        "metadata": metadata or {},
    }
    try:
        resp = requests.post(
            f"{url}/v1/documents",
            json=payload,
            headers=_headers(api_key),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.error("KB ingest error: %s", e)
        return None


def kb_delete(env, document_id):
    """Delete a document from the knowledge base."""
    url, api_key = _get_config(env)
    try:
        resp = requests.delete(
            f"{url}/v1/documents/{document_id}",
            headers=_headers(api_key),
            timeout=DEFAULT_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.error("KB delete error: %s", e)
        return None
