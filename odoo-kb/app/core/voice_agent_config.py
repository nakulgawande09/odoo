"""Voice agent configuration store.

Holds agent configurations pushed from Odoo. The VOIP endpoints
read from this store at runtime to customize behavior per agent
(greeting, escalation, confidence threshold, etc.).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class VoiceAgentConfig:
    """Configuration for a single voice agent."""
    agent_id: int
    name: str = "Default Voice Agent"
    provider: str = "generic"
    greeting_message: str = "Hello! How can I help you today?"
    no_answer_message: str = (
        "I couldn't find information about that. "
        "Would you like me to connect you with a support agent?"
    )
    low_confidence_message: str = (
        "I'm not fully confident in my answer. "
        "Let me transfer you to someone who can help."
    )
    goodbye_message: str = "Thank you for calling. Have a great day!"
    follow_up_enabled: bool = True
    tts_voice: str = "default"
    tts_language: str = "en-US"
    tts_speed: float = 1.0
    max_results: int = 3
    confidence_threshold: float = 0.3
    max_answer_length: int = 500
    escalation_mode: str = "transfer"
    escalation_number: str = ""
    escalation_message: str = "Let me connect you with a live agent. Please hold."
    # Gemini Live extras (optional; populated by Odoo `Sync to KB Service`).
    system_prompt: str = ""
    live_voice: str = "Aoede"
    live_model: str = ""  # blank → fall back to settings.live_model
    kb_search_instruction: str = ""


# Default config used when no agent_id is specified
DEFAULT_CONFIG = VoiceAgentConfig(agent_id=0)


class VoiceAgentStore:
    """In-memory store for voice agent configurations.

    Configs are pushed from Odoo via PUT /v1/voip/agents/{id}.
    """

    def __init__(self) -> None:
        self._agents: dict[int, VoiceAgentConfig] = {}

    def get(self, agent_id: int | None) -> VoiceAgentConfig:
        """Get agent config by ID, or return default."""
        if agent_id is None or agent_id not in self._agents:
            return DEFAULT_CONFIG
        return self._agents[agent_id]

    def put(self, agent_id: int, config: dict[str, Any]) -> VoiceAgentConfig:
        """Create or update an agent config."""
        agent = VoiceAgentConfig(agent_id=agent_id, **{
            k: v for k, v in config.items()
            if k in VoiceAgentConfig.__dataclass_fields__ and k != "agent_id"
        })
        self._agents[agent_id] = agent
        logger.info("Updated voice agent config: %s (id=%d)", agent.name, agent_id)
        return agent

    def delete(self, agent_id: int) -> bool:
        """Remove an agent config."""
        if agent_id in self._agents:
            del self._agents[agent_id]
            return True
        return False

    def list_all(self) -> list[VoiceAgentConfig]:
        """Return all configured agents."""
        return list(self._agents.values())
