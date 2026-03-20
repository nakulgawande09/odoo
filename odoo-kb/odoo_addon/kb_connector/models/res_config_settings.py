"""Settings UI for KB service and VOIP configuration."""
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # KB Service
    kb_service_url = fields.Char(
        "Knowledge Base Service URL",
        config_parameter="kb.service.url",
        default="http://localhost:8100",
    )
    kb_service_api_key = fields.Char(
        "Knowledge Base API Key",
        config_parameter="kb.service.api_key",
    )

    # VOIP Integration
    kb_voip_enabled = fields.Boolean(
        "Enable VOIP Integration",
        config_parameter="kb.voip.enabled",
        default=False,
    )
    kb_voip_provider = fields.Selection(
        [
            ("twilio", "Twilio"),
            ("vonage", "Vonage (Nexmo)"),
            ("asterisk", "Asterisk / FreePBX"),
            ("generic", "Generic SIP / Other"),
        ],
        string="Default VOIP Provider",
        config_parameter="kb.voip.provider",
        default="generic",
    )

    # Twilio
    kb_twilio_account_sid = fields.Char(
        "Twilio Account SID",
        config_parameter="kb.twilio.account_sid",
    )
    kb_twilio_auth_token = fields.Char(
        "Twilio Auth Token",
        config_parameter="kb.twilio.auth_token",
    )

    # Vonage
    kb_vonage_api_key = fields.Char(
        "Vonage API Key",
        config_parameter="kb.vonage.api_key",
    )
    kb_vonage_api_secret = fields.Char(
        "Vonage API Secret",
        config_parameter="kb.vonage.api_secret",
    )
