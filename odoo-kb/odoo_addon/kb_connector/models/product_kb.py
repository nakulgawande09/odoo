"""Extend product.template with KB mixin for indexing products."""

from odoo import models


class ProductTemplateKB(models.Model):
    _name = "product.template"
    _inherit = ["product.template", "kb.mixin"]

    _kb_content_fields = ["name", "description", "description_sale"]
    _kb_title_field = "name"
    _kb_tags = ["product"]
