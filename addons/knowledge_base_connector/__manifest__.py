# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Knowledge Base Connector',
    'version': '1.0',
    'category': 'Productivity',
    'summary': 'Connect Odoo to the external Knowledge Base search service',
    'description': """
Knowledge Base Connector
========================
Integrates Odoo with the Knowledge Base microservice for semantic search,
document sync, and helpdesk/livechat knowledge retrieval.

Features:
- Search the KB directly from Odoo (systray widget)
- Auto-sync Odoo records (products, helpdesk articles) to the KB
- Use KB answers in livechat and helpdesk
- Configurable KB service URL and API key
    """,
    'depends': ['base_setup', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_view.xml',
        'views/kb_document_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'knowledge_base_connector/static/src/scss/kb_search.scss',
            'knowledge_base_connector/static/src/js/kb_search_service.js',
            'knowledge_base_connector/static/src/js/kb_search_widget.js',
            'knowledge_base_connector/static/src/xml/kb_search_widget.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
