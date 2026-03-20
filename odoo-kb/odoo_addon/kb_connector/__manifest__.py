{
    "name": "Knowledge Base Connector",
    "version": "18.0.1.0.0",
    "category": "Productivity",
    "summary": "Connect Odoo to the Knowledge Base microservice",
    "description": """
        Thin integration layer between Odoo and the KB microservice.
        Provides:
        - kb.mixin: add to any model to make it KB-indexable
        - kb_client: Python API to call the KB service
        - Settings UI for KB service URL and API key
    """,
    "depends": ["base", "mail"],
    "data": [
        "data/ir_config_parameter.xml",
    ],
    "installable": True,
    "auto_install": False,
    "license": "LGPL-3",
}
