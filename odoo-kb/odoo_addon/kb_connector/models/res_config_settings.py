"""Settings UI for KB service configuration."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    kb_service_url = fields.Char(
        "Knowledge Base Service URL",
        config_parameter="kb.service.url",
        default="http://localhost:8100",
    )
    kb_service_api_key = fields.Char(
        "Knowledge Base API Key",
        config_parameter="kb.service.api_key",
    )
