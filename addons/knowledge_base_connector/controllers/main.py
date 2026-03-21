# -*- coding: utf-8 -*-
from odoo import http
from odoo.http import request


class KBSearchController(http.Controller):
    """JSON-RPC endpoints for the KB search widget."""

    @http.route('/kb/search', type='jsonrpc', auth='user')
    def kb_search(self, query, limit=5, **kw):
        """Proxy search to the KB service, called by the frontend widget."""
        result = request.env['kb.document'].search_kb(query, limit=limit, source='odoo')
        return result

    @http.route('/kb/sync', type='jsonrpc', auth='user')
    def kb_sync_record(self, model, res_id, title, content, tags=None, **kw):
        """Sync a record to the KB from the frontend."""
        doc = request.env['kb.document'].sync_odoo_record(
            model, res_id, title, content, tags=tags or [],
        )
        return {
            'id': doc.id,
            'kb_document_id': doc.kb_document_id,
            'state': doc.state,
        }
