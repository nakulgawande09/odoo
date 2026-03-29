{
    "name": "Knowledge Base Connector",
    "version": "19.0.2.0.0",
    "category": "Productivity",
    "summary": "Connect Odoo to the Knowledge Base microservice with VOIP support",
    "description": """
        Integration layer between Odoo and the KB microservice.
        Provides:
        - kb.mixin: add to any model to make it KB-indexable
        - kb_client: Python API to call the KB service
        - Settings UI for KB service URL, API key, and VOIP providers
        - Voice Agent configuration for Twilio, Vonage, Asterisk
        - Webhook URL generation and one-click test buttons
    """,
    "depends": ["base", "mail", "product"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "views/kb_document_views.xml",
        "views/kb_voice_agent_views.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
    "auto_install": False,
    "license": "LGPL-3",
}
