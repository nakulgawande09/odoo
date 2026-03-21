# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    kb_service_url = fields.Char(
        string="KB Service URL",
        config_parameter='knowledge_base_connector.service_url',
        default="http://localhost:8100",
        help="Base URL of the Knowledge Base microservice.",
    )
    kb_api_key = fields.Char(
        string="KB API Key",
        config_parameter='knowledge_base_connector.api_key',
        help="API key for authenticating with the KB service.",
    )
    kb_auto_sync = fields.Boolean(
        string="Auto-sync Documents",
        config_parameter='knowledge_base_connector.auto_sync',
        default=False,
        help="Automatically push new/updated records to the KB.",
    )
    kb_sync_models = fields.Char(
        string="Models to Sync",
        config_parameter='knowledge_base_connector.sync_models',
        default="",
        help="Comma-separated list of Odoo models to sync (e.g. product.template,helpdesk.article).",
    )
