# -*- coding: utf-8 -*-
import json
import logging

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Timeout for KB service HTTP calls
_TIMEOUT = 15


class KBDocument(models.Model):
    """Tracks documents synced to the external KB service."""
    _name = 'kb.document'
    _description = 'Knowledge Base Document'
    _order = 'write_date desc'

    name = fields.Char(string="Title", required=True)
    kb_document_id = fields.Char(string="KB Document ID", readonly=True, index=True)
    content = fields.Text(string="Content")
    content_type = fields.Selection([
        ('text/plain', 'Plain Text'),
        ('text/html', 'HTML'),
        ('text/csv', 'CSV'),
    ], string="Content Type", default='text/plain')
    source_model = fields.Char(string="Source Model", help="e.g. product.template")
    source_id = fields.Integer(string="Source Record ID")
    tags = fields.Char(string="Tags", help="Comma-separated tags")
    state = fields.Selection([
        ('draft', 'Draft'),
        ('synced', 'Synced'),
        ('error', 'Error'),
    ], string="Status", default='draft', readonly=True)
    error_message = fields.Text(string="Error", readonly=True)

    def _get_kb_config(self):
        """Read KB service configuration from system parameters."""
        ICP = self.env['ir.config_parameter'].sudo()
        url = ICP.get_param('knowledge_base_connector.service_url', 'http://localhost:8100')
        api_key = ICP.get_param('knowledge_base_connector.api_key', '')
        return url.rstrip('/'), api_key

    def _kb_headers(self, api_key):
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['Authorization'] = f'Bearer {api_key}'
        return headers

    def action_sync_to_kb(self):
        """Push this document to the KB service."""
        for rec in self:
            rec._sync_one()

    def _sync_one(self):
        """Sync a single document to the KB service (create or update)."""
        url, api_key = self._get_kb_config()
        headers = self._kb_headers(api_key)
        tag_list = [t.strip() for t in (self.tags or '').split(',') if t.strip()]

        payload = {
            'content': self.content or '',
            'content_type': self.content_type,
            'title': self.name,
            'tags': tag_list,
            'metadata': {
                'odoo_model': self.source_model or '',
                'odoo_id': self.source_id or 0,
            },
            'source_ref': f'{self.source_model}:{self.source_id}' if self.source_model else '',
        }

        try:
            if self.kb_document_id:
                # Update existing
                resp = requests.put(
                    f'{url}/v1/documents/{self.kb_document_id}',
                    headers=headers, json=payload, timeout=_TIMEOUT,
                )
            else:
                # Create new
                resp = requests.post(
                    f'{url}/v1/documents',
                    headers=headers, json=payload, timeout=_TIMEOUT,
                )

            resp.raise_for_status()
            data = resp.json()
            self.write({
                'kb_document_id': data.get('id', self.kb_document_id),
                'state': 'synced',
                'error_message': False,
            })
        except requests.RequestException as e:
            self.write({
                'state': 'error',
                'error_message': str(e),
            })
            _logger.warning("KB sync failed for %s: %s", self.name, e)

    def action_delete_from_kb(self):
        """Delete this document from the KB service."""
        for rec in self.filtered('kb_document_id'):
            url, api_key = rec._get_kb_config()
            headers = rec._kb_headers(api_key)
            try:
                resp = requests.delete(
                    f'{url}/v1/documents/{rec.kb_document_id}',
                    headers=headers, timeout=_TIMEOUT,
                )
                resp.raise_for_status()
                rec.write({'state': 'draft', 'kb_document_id': False})
            except requests.RequestException as e:
                _logger.warning("KB delete failed for %s: %s", rec.name, e)

    @api.model
    def search_kb(self, query, limit=5, source='odoo'):
        """Search the KB service and return results."""
        url, api_key = self._get_kb_config()
        headers = self._kb_headers(api_key)
        try:
            resp = requests.post(
                f'{url}/v1/search',
                headers=headers,
                json={'query': query, 'limit': limit, 'source': source},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            _logger.warning("KB search failed: %s", e)
            return {'results': [], 'total_count': 0, 'error': str(e)}

    @api.model
    def sync_odoo_record(self, model_name, record_id, title, content, tags=None):
        """Sync an arbitrary Odoo record to the KB.

        Called by automation rules or other modules.
        """
        existing = self.search([
            ('source_model', '=', model_name),
            ('source_id', '=', record_id),
        ], limit=1)

        vals = {
            'name': title,
            'content': content,
            'source_model': model_name,
            'source_id': record_id,
            'tags': ','.join(tags or []),
        }

        if existing:
            existing.write(vals)
            existing._sync_one()
            return existing
        else:
            doc = self.create(vals)
            doc._sync_one()
            return doc
