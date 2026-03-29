"""Knowledge Base Document model.

Standalone model for managing documents in the KB service.
Users can create content via the rich text editor, upload files
(PDF, CSV, HTML, text), and push them to the KB service for indexing.
"""
import base64
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

DOCUMENT_TYPES = [
    ("faq", "FAQ"),
    ("article", "Article"),
    ("product_info", "Product Info"),
    ("policy", "Policy"),
    ("procedure", "Procedure"),
    ("custom", "Custom"),
]

KB_STATUS = [
    ("draft", "Draft"),
    ("indexed", "Indexed"),
    ("failed", "Failed"),
]

CONTENT_TYPE_MAP = {
    "application/pdf": "application/pdf",
    "text/csv": "text/csv",
    "text/html": "text/html",
    "text/plain": "text/plain",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "text/plain",
}


class KBDocument(models.Model):
    _name = "kb.document"
    _description = "Knowledge Base Document"
    _order = "write_date desc"

    name = fields.Char("Title", required=True)
    content = fields.Html(
        "Content",
        help="Write or paste content here. Supports rich text formatting.",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "kb_document_attachment_rel",
        "document_id",
        "attachment_id",
        string="Files",
        help="Upload PDF, CSV, HTML, or text files to index.",
    )
    document_type = fields.Selection(
        DOCUMENT_TYPES,
        string="Type",
        default="article",
        required=True,
    )
    tags = fields.Char(
        "Tags",
        help="Comma-separated tags for categorization.",
    )
    auto_sync = fields.Boolean(
        "Auto-sync on save",
        default=True,
        help="Automatically push to KB service when saved.",
    )

    # KB service state (read-only, set by push/remove actions)
    kb_document_ids = fields.Text(
        "KB Document IDs",
        readonly=True,
        help="JSON list of document IDs in the KB service.",
    )
    kb_status = fields.Selection(
        KB_STATUS,
        string="KB Status",
        default="draft",
        readonly=True,
    )
    kb_chunks_count = fields.Integer("Chunks", readonly=True)
    kb_last_sync = fields.Datetime("Last Synced", readonly=True)

    notes = fields.Text("Internal Notes")

    def _get_tag_list(self):
        """Parse comma-separated tags into a list."""
        self.ensure_one()
        if not self.tags:
            return [self.document_type]
        tag_list = [t.strip() for t in self.tags.split(",") if t.strip()]
        if self.document_type not in tag_list:
            tag_list.append(self.document_type)
        return tag_list

    def _extract_html_text(self, html_content):
        """Strip HTML tags to get plain text for content type detection."""
        if not html_content:
            return ""
        try:
            from html.parser import HTMLParser
            from io import StringIO

            class _HTMLStripper(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self._text = StringIO()

                def handle_data(self, data):
                    self._text.write(data)

                def get_text(self):
                    return self._text.getvalue()

            stripper = _HTMLStripper()
            stripper.feed(str(html_content))
            return stripper.get_text().strip()
        except Exception:
            return str(html_content)

    def action_push_to_kb(self):
        """Push this document's content and attachments to the KB service."""
        from odoo.addons.kb_connector.tools.kb_client import kb_ingest

        for record in self:
            pushed_ids = []
            total_chunks = 0
            tag_list = record._get_tag_list()
            source_ref = f"kb.document:{record.id}"

            # Push HTML content if present
            text_content = record._extract_html_text(record.content)
            if text_content:
                result = kb_ingest(
                    self.env,
                    content=str(record.content),
                    title=record.name,
                    content_type="text/html",
                    tags=tag_list,
                    source_ref=source_ref,
                )
                if result:
                    pushed_ids.append(result.get("id", ""))
                    total_chunks += result.get("chunks_count", 0)

            # Push each attachment
            for attachment in record.attachment_ids:
                file_content = base64.b64decode(attachment.datas) if attachment.datas else b""
                if not file_content:
                    continue

                mimetype = attachment.mimetype or "text/plain"
                content_type = CONTENT_TYPE_MAP.get(mimetype, "text/plain")

                try:
                    decoded = file_content.decode("utf-8", errors="replace")
                except Exception:
                    decoded = file_content.decode("latin-1", errors="replace")

                result = kb_ingest(
                    self.env,
                    content=decoded,
                    title=f"{record.name} - {attachment.name}",
                    content_type=content_type,
                    tags=tag_list,
                    source_ref=source_ref,
                )
                if result:
                    pushed_ids.append(result.get("id", ""))
                    total_chunks += result.get("chunks_count", 0)

            if pushed_ids:
                import json
                record.write({
                    "kb_document_ids": json.dumps(pushed_ids),
                    "kb_status": "indexed",
                    "kb_chunks_count": total_chunks,
                    "kb_last_sync": fields.Datetime.now(),
                })
            elif text_content or record.attachment_ids:
                record.write({"kb_status": "failed"})
            else:
                raise UserError(
                    _("No content to push. Add text content or upload files first.")
                )

    def action_remove_from_kb(self):
        """Remove this document from the KB service."""
        from odoo.addons.kb_connector.tools.kb_client import kb_delete
        import json

        for record in self:
            if not record.kb_document_ids:
                continue

            try:
                doc_ids = json.loads(record.kb_document_ids)
            except (json.JSONDecodeError, TypeError):
                doc_ids = []

            for doc_id in doc_ids:
                if doc_id:
                    kb_delete(self.env, doc_id)

            record.write({
                "kb_document_ids": False,
                "kb_status": "draft",
                "kb_chunks_count": 0,
                "kb_last_sync": False,
            })

    def action_push_all_draft(self):
        """Batch push all draft documents to the KB service."""
        drafts = self.search([("kb_status", "=", "draft")])
        drafts.action_push_to_kb()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Batch Push Complete"),
                "message": _("%d documents pushed to KB service.") % len(drafts),
                "type": "success",
                "sticky": False,
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            if record.auto_sync and (record.content or record.attachment_ids):
                try:
                    record.action_push_to_kb()
                except Exception as e:
                    logger.warning("Auto-sync failed on create for %s: %s", record.name, e)
        return records

    def write(self, vals):
        result = super().write(vals)
        content_fields = {"content", "attachment_ids", "name", "tags", "document_type"}
        if content_fields & set(vals.keys()):
            for record in self:
                if record.auto_sync and (record.content or record.attachment_ids):
                    try:
                        # Remove old version first, then push new
                        record.action_remove_from_kb()
                        record.action_push_to_kb()
                    except Exception as e:
                        logger.warning("Auto-sync failed on write for %s: %s", record.name, e)
        return result
