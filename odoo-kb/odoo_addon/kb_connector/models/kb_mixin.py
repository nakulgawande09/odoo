"""Mixin for Odoo models that should be indexed in the Knowledge Base."""
import logging

from odoo import api, fields, models

logger = logging.getLogger(__name__)


class KBMixin(models.AbstractModel):
    _name = "kb.mixin"
    _description = "Knowledge Base Mixin"

    kb_indexed = fields.Boolean("KB Indexed", default=False, copy=False)
    kb_document_id = fields.Char("KB Document ID", copy=False)

    # Override in concrete models
    _kb_content_fields = []  # list of field names to concatenate as content
    _kb_title_field = "name"  # field to use as document title
    _kb_tags = []  # static tags for this model

    def _kb_get_content(self):
        """Build indexable text content from configured fields.

        Override this in concrete models for custom content extraction.
        """
        self.ensure_one()
        parts = []
        for field_name in self._kb_content_fields:
            value = self[field_name]
            if value:
                parts.append(str(value))
        return "\n\n".join(parts)

    def _kb_get_title(self):
        self.ensure_one()
        return str(self[self._kb_title_field] or "")

    def action_push_to_kb(self):
        """Push selected records to the KB service."""
        from odoo.addons.kb_connector.tools.kb_client import kb_ingest

        for record in self:
            content = record._kb_get_content()
            title = record._kb_get_title()
            if not content:
                continue
            result = kb_ingest(
                self.env,
                content=content,
                title=title,
                content_type="text/html",
                tags=record._kb_tags,
                source_ref=f"{record._name}:{record.id}",
            )
            if result:
                record.write({
                    "kb_document_id": result.get("id"),
                    "kb_indexed": True,
                })

    def action_remove_from_kb(self):
        """Remove selected records from the KB service."""
        from odoo.addons.kb_connector.tools.kb_client import kb_delete

        for record in self:
            if record.kb_document_id:
                kb_delete(self.env, record.kb_document_id)
                record.write({
                    "kb_document_id": False,
                    "kb_indexed": False,
                })
