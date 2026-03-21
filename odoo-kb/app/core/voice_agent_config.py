"""Voice agent configuration store.

Holds agent configurations pushed from Odoo. The VOIP endpoints
read from this store at runtime to customize behavior per agent
(greeting, escalation, confidence threshold, etc.).

Two implementations:
  - VoiceAgentStore: in-memory (dev / single instance)
  - RedisVoiceAgentStore: Redis-backed (production / multi-instance)
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
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

    # RAG answer synthesis
    rag_enabled: bool = False
    rag_llm_provider: str = "openai"
    rag_llm_model: str = "gpt-4o-mini"
    rag_system_prompt: str = ""
    rag_max_tokens: int = 300
    rag_temperature: float = 0.3
    rag_max_context_chunks: int = 5

    def to_rag_config(self) -> "RAGConfig":
        """Build a RAGConfig from this agent's settings."""
        from app.core.rag_config import RAGConfig

        return RAGConfig(
            enabled=self.rag_enabled,
            llm_provider=self.rag_llm_provider,
            llm_model=self.rag_llm_model,
            system_prompt=self.rag_system_prompt,
            max_tokens=self.rag_max_tokens,
            temperature=self.rag_temperature,
            max_context_chunks=self.rag_max_context_chunks,
            channel="voip",
        )


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


class RedisVoiceAgentStore:
    """Redis-backed voice agent config store.

    Survives restarts and is shared across multiple service instances.
    Each agent config is stored as a Redis hash at key ``kb:agent:{id}``.
    """

    _KEY_PREFIX = "kb:agent:"

    def __init__(self, redis: Any) -> None:
        self._r = redis

    async def get(self, agent_id: int | None) -> VoiceAgentConfig:
        if agent_id is None:
            return DEFAULT_CONFIG
        raw = await self._r.get(f"{self._KEY_PREFIX}{agent_id}")
        if raw is None:
            return DEFAULT_CONFIG
        data = json.loads(raw)
        return VoiceAgentConfig(**data)

    async def put(self, agent_id: int, config: dict[str, Any]) -> VoiceAgentConfig:
        agent = VoiceAgentConfig(agent_id=agent_id, **{
            k: v for k, v in config.items()
            if k in VoiceAgentConfig.__dataclass_fields__ and k != "agent_id"
        })
        await self._r.set(
            f"{self._KEY_PREFIX}{agent_id}",
            json.dumps(asdict(agent)),
        )
        logger.info("Updated voice agent config (Redis): %s (id=%d)", agent.name, agent_id)
        return agent

    async def delete(self, agent_id: int) -> bool:
        deleted = await self._r.delete(f"{self._KEY_PREFIX}{agent_id}")
        return deleted > 0

    async def list_all(self) -> list[VoiceAgentConfig]:
        agents: list[VoiceAgentConfig] = []
        cursor = 0
        while True:
            cursor, keys = await self._r.scan(cursor, match=f"{self._KEY_PREFIX}*", count=100)
            for key in keys:
                raw = await self._r.get(key)
                if raw:
                    agents.append(VoiceAgentConfig(**json.loads(raw)))
            if cursor == 0:
                break
        return agents
