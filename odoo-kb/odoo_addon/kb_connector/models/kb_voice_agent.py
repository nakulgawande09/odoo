"""Voice Agent configuration model.

Each voice agent defines how the KB service responds during VOIP calls:
greeting message, fallback behavior, TTS settings, escalation rules,
and which VOIP provider to use.

Agents are managed via the Odoo UI and synced to the KB service
as JSON config so the webhook endpoints can use them at runtime.
"""
import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

VOIP_PROVIDERS = [
    ("twilio", "Twilio"),
    ("vonage", "Vonage (Nexmo)"),
    ("asterisk", "Asterisk / FreePBX"),
    ("generic", "Generic SIP / Other"),
]

TTS_VOICES = [
    ("default", "Default"),
    ("male", "Male"),
    ("female", "Female"),
    ("neural_female", "Neural Female (Premium)"),
    ("neural_male", "Neural Male (Premium)"),
]

ESCALATION_MODES = [
    ("none", "No Escalation"),
    ("transfer", "Transfer to Agent"),
    ("voicemail", "Send to Voicemail"),
    ("callback", "Schedule Callback"),
]


class KBVoiceAgent(models.Model):
    _name = "kb.voice.agent"
    _description = "KB Voice Agent Configuration"
    _order = "sequence, name"

    name = fields.Char("Agent Name", required=True, default="Default Voice Agent")
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    # Provider settings
    voip_provider = fields.Selection(
        VOIP_PROVIDERS,
        string="VOIP Provider",
        required=True,
        default="generic",
    )

    # Provider credentials
    twilio_account_sid = fields.Char("Twilio Account SID")
    twilio_auth_token = fields.Char("Twilio Auth Token")
    vonage_api_key = fields.Char("Vonage API Key")
    vonage_api_secret = fields.Char("Vonage API Secret")

    # Webhook URLs (computed)
    webhook_url = fields.Char(
        "Webhook URL",
        compute="_compute_webhook_url",
        store=False,
    )
    webhook_url_display = fields.Char(
        "Webhook URL (Copy)",
        compute="_compute_webhook_url",
        store=False,
    )

    # Voice agent behavior
    greeting_message = fields.Text(
        "Greeting Message",
        default="Hello! I'm a virtual assistant. How can I help you today?",
    )
    no_answer_message = fields.Text(
        "No Answer Found Message",
        default="I couldn't find information about that in our knowledge base. "
                "Would you like me to connect you with a support agent?",
    )
    low_confidence_message = fields.Text(
        "Low Confidence Message",
        default="I'm not fully confident in my answer. "
                "Let me transfer you to someone who can help better.",
    )
    goodbye_message = fields.Text(
        "Goodbye Message",
        default="Thank you for calling. Have a great day!",
    )
    follow_up_enabled = fields.Boolean(
        "Enable Follow-up Questions",
        default=True,
        help="Suggest follow-up questions after answering.",
    )

    # TTS settings
    tts_voice = fields.Selection(
        TTS_VOICES,
        string="TTS Voice",
        default="default",
    )
    tts_language = fields.Char("TTS Language Code", default="en-US")
    tts_speed = fields.Float("TTS Speed", default=1.0)

    # Search settings
    max_results = fields.Integer("Max Search Results", default=3)
    confidence_threshold = fields.Float(
        "Confidence Threshold",
        default=0.3,
        help="Below this score, trigger escalation instead of answering.",
    )
    max_answer_length = fields.Integer(
        "Max Answer Length (chars)",
        default=500,
        help="Truncate answers for TTS. Longer answers are cut at sentence boundaries.",
    )

    # Escalation
    escalation_mode = fields.Selection(
        ESCALATION_MODES,
        string="Escalation Mode",
        default="transfer",
    )
    escalation_number = fields.Char(
        "Escalation Phone Number",
        help="Phone number to transfer calls when escalation is triggered.",
    )
    escalation_message = fields.Text(
        "Escalation Message",
        default="Let me connect you with a live agent. Please hold.",
    )

    # Working hours
    active_hours_enabled = fields.Boolean(
        "Restrict to Working Hours",
        default=False,
    )
    active_hours_start = fields.Float("Start Hour", default=9.0)
    active_hours_end = fields.Float("End Hour", default=17.0)
    timezone = fields.Selection(
        "_tz_list",
        string="Timezone",
        default="UTC",
    )

    # Stats (read-only, updated by KB service)
    total_calls = fields.Integer("Total Calls", readonly=True, default=0)
    avg_confidence = fields.Float("Avg Confidence", readonly=True, digits=(4, 2))
    last_call_at = fields.Datetime("Last Call", readonly=True)

    # Notes
    notes = fields.Text("Internal Notes")

    @api.model
    def _tz_list(self):
        """Return timezone selection list."""
        import pytz
        return [(tz, tz) for tz in sorted(pytz.common_timezones)]

    @api.depends("voip_provider")
    def _compute_webhook_url(self):
        """Generate the webhook URL for this agent's provider."""
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("kb.service.url", "http://localhost:8100")

        provider_paths = {
            "twilio": "/v1/voip/twilio",
            "vonage": "/v1/voip/vonage",
            "asterisk": "/v1/voip/sip",
            "generic": "/v1/voip/query",
        }

        for record in self:
            path = provider_paths.get(record.voip_provider, "/v1/voip/query")
            url = f"{base_url}{path}?agent_id={record.id}"
            record.webhook_url = url
            record.webhook_url_display = url

    def action_test_connection(self):
        """Test connectivity to the KB service."""
        self.ensure_one()
        import requests

        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("kb.service.url", "http://localhost:8100")
        api_key = icp.get_param("kb.service.api_key", "")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            resp = requests.get(
                f"{base_url}/v1/health",
                headers=headers,
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") == "healthy":
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("Connection Successful"),
                        "message": _("KB service is healthy and responding."),
                        "type": "success",
                        "sticky": False,
                    },
                }
            else:
                raise UserError(
                    _("KB service responded but status is: %s") % data.get("status")
                )
        except requests.exceptions.ConnectionError:
            raise UserError(
                _("Cannot connect to KB service at %s. "
                  "Make sure the service is running.") % base_url
            )
        except requests.exceptions.Timeout:
            raise UserError(_("KB service timed out."))

    def action_test_voice_query(self):
        """Send a test query through the VOIP endpoint."""
        self.ensure_one()
        import requests

        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("kb.service.url", "http://localhost:8100")
        api_key = icp.get_param("kb.service.api_key", "")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            resp = requests.post(
                f"{base_url}/v1/voip/query",
                json={
                    "query": "What is your return policy?",
                    "provider": self.voip_provider,
                    "call_id": f"test-{self.id}",
                    "max_results": self.max_results,
                },
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Test Query Successful"),
                    "message": _(
                        "Answer (confidence %.0f%%): %s"
                    ) % (data.get("confidence", 0) * 100, data.get("answer", "")[:200]),
                    "type": "success",
                    "sticky": True,
                },
            }
        except requests.exceptions.RequestException as e:
            raise UserError(_("Test query failed: %s") % str(e))

    def action_sync_config(self):
        """Push this agent's config to the KB service."""
        self.ensure_one()
        import requests

        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("kb.service.url", "http://localhost:8100")
        api_key = icp.get_param("kb.service.api_key", "")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        config = self._get_agent_config()

        try:
            resp = requests.put(
                f"{base_url}/v1/voip/agents/{self.id}",
                json=config,
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("Config Synced"),
                    "message": _("Voice agent configuration pushed to KB service."),
                    "type": "success",
                    "sticky": False,
                },
            }
        except requests.exceptions.RequestException as e:
            raise UserError(_("Config sync failed: %s") % str(e))

    def _get_agent_config(self):
        """Serialize agent config as dict for the KB service."""
        self.ensure_one()
        return {
            "agent_id": self.id,
            "name": self.name,
            "provider": self.voip_provider,
            "greeting_message": self.greeting_message,
            "no_answer_message": self.no_answer_message,
            "low_confidence_message": self.low_confidence_message,
            "goodbye_message": self.goodbye_message,
            "follow_up_enabled": self.follow_up_enabled,
            "tts_voice": self.tts_voice,
            "tts_language": self.tts_language,
            "tts_speed": self.tts_speed,
            "max_results": self.max_results,
            "confidence_threshold": self.confidence_threshold,
            "max_answer_length": self.max_answer_length,
            "escalation_mode": self.escalation_mode,
            "escalation_number": self.escalation_number,
            "escalation_message": self.escalation_message,
        }
