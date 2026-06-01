"""Pre-migration to 19.0.2.1.0: add the Gemini Live fields to kb_voice_agent.

Odoo's ORM creates new columns on module upgrade automatically, but if the
upgrade is interrupted (or if some other path reads the table before the
ORM finishes its schema sync — e.g. computed display_name during web_read)
the missing columns surface as `psycopg2.errors.UndefinedColumn` and the
whole request fails.

This pre-migration runs BEFORE the ORM touches the schema and ensures the
three new columns exist. `ADD COLUMN IF NOT EXISTS` is idempotent — safe
to re-run, safe to apply on a fresh install (where the ORM would create
them anyway).
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Fresh install — ORM will create the columns. Nothing to do.
        return

    _logger.info("kb_connector: ensuring Gemini Live columns exist on kb_voice_agent")
    cr.execute(
        """
        ALTER TABLE kb_voice_agent
            ADD COLUMN IF NOT EXISTS system_prompt          TEXT,
            ADD COLUMN IF NOT EXISTS live_voice             VARCHAR,
            ADD COLUMN IF NOT EXISTS kb_search_instruction  TEXT
        """
    )
    cr.execute(
        """
        UPDATE kb_voice_agent
           SET live_voice = 'Aoede'
         WHERE live_voice IS NULL
        """
    )
