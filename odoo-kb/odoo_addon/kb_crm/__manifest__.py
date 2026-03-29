{
    "name": "KB CRM Integration",
    "version": "19.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "AI-powered CRM lead creation from voice calls and KB interactions",
    "description": """
        Extends CRM leads with fields from the KB voice agent:
        - AI conversation summary and sentiment analysis
        - Customer intent classification and priority scoring
        - Call transcript and recording links
        - Pull-based sync: Odoo polls KB service for analyzed calls
        - Cron job creates CRM leads automatically (no KB-side credentials needed)
    """,
    "depends": ["crm", "kb_connector"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/crm_lead_views.xml",
    ],
    "installable": True,
    "auto_install": False,
    "license": "LGPL-3",
}
