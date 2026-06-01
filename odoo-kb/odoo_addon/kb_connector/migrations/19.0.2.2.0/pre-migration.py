"""Pre-migration to 19.0.2.2.0: add `live_model` to kb_voice_agent.

Lets ops pick the Gemini Live model per-agent (overrides the service-wide
KB_LIVE_MODEL default). `ADD COLUMN IF NOT EXISTS` keeps this idempotent
and safe on fresh installs.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _logger.info("kb_connector: ensuring live_model column exists on kb_voice_agent")
    cr.execute(
        "ALTER TABLE kb_voice_agent "
        "ADD COLUMN IF NOT EXISTS live_model VARCHAR"
    )
