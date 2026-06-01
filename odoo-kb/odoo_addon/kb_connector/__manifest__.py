{
    "name": "Knowledge Base Connector",
    "version": "19.0.2.2.0",
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
        "wizard/kb_test_console_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "kb_connector/static/src/js/voice_input_field.js",
            "kb_connector/static/src/xml/voice_input_field.xml",
            "kb_connector/static/src/js/auto_tts_field.js",
            "kb_connector/static/src/xml/auto_tts_field.xml",
            "kb_connector/static/src/js/live_call_field.js",
            "kb_connector/static/src/xml/live_call_field.xml",
        ],
        # Note: live_audio_worklet.js is intentionally NOT in any asset
        # bundle — AudioWorklet modules must be loaded standalone by URL
        # (audioContext.audioWorklet.addModule), not as part of a bundle.
        # Odoo serves files under static/ automatically, so the worklet
        # is reachable at /kb_connector/static/src/js/live_audio_worklet.js.
    },
    "installable": True,
    "auto_install": False,
    "license": "LGPL-3",
}
