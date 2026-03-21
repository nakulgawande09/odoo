"""Multi-provider LLM abstraction for RAG answer synthesis.

Supports OpenAI, Anthropic, and Ollama (local) with a factory function.
Provider SDKs are imported lazily to keep them optional.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


class OpenAILLMProvider:
    """LLM provider using the OpenAI chat completions API."""

    def __init__(self, api_key: str, default_model: str = "gpt-4o-mini") -> None:
        self._api_key = api_key
        self._default_model = default_model

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)
        response = await client.chat.completions.create(
            model=kwargs.get("model", self._default_model),
            messages=messages,
            max_tokens=kwargs.get("max_tokens", 300),
            temperature=kwargs.get("temperature", 0.3),
        )
        return response.choices[0].message.content or ""

    async def generate_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key)
        stream = await client.chat.completions.create(
            model=kwargs.get("model", self._default_model),
            messages=messages,
            max_tokens=kwargs.get("max_tokens", 300),
            temperature=kwargs.get("temperature", 0.3),
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content


class AnthropicLLMProvider:
    """LLM provider using the Anthropic messages API."""

    def __init__(self, api_key: str, default_model: str = "claude-sonnet-4-20250514") -> None:
        self._api_key = api_key
        self._default_model = default_model

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self._api_key)

        # Separate system message from user/assistant messages
        system_text = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
            else:
                chat_messages.append(msg)

        response = await client.messages.create(
            model=kwargs.get("model", self._default_model),
            max_tokens=kwargs.get("max_tokens", 300),
            system=system_text or "You are a helpful assistant.",
            messages=chat_messages,
        )
        return response.content[0].text if response.content else ""

    async def generate_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self._api_key)

        system_text = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
            else:
                chat_messages.append(msg)

        async with client.messages.stream(
            model=kwargs.get("model", self._default_model),
            max_tokens=kwargs.get("max_tokens", 300),
            system=system_text or "You are a helpful assistant.",
            messages=chat_messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text


class OllamaLLMProvider:
    """LLM provider using the local Ollama HTTP API."""

    def __init__(
        self, base_url: str = "http://localhost:11434", default_model: str = "llama3"
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model

    async def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        import httpx

        model = kwargs.get("model", self._default_model)
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "num_predict": kwargs.get("max_tokens", 300),
                        "temperature": kwargs.get("temperature", 0.3),
                    },
                },
            )
            response.raise_for_status()
            data = response.json()
            return data.get("message", {}).get("content", "")

    async def generate_stream(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        import httpx

        model = kwargs.get("model", self._default_model)
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "num_predict": kwargs.get("max_tokens", 300),
                        "temperature": kwargs.get("temperature", 0.3),
                    },
                },
            ) as response:
                import json

                async for line in response.aiter_lines():
                    if line.strip():
                        chunk = json.loads(line)
                        content = chunk.get("message", {}).get("content", "")
                        if content:
                            yield content


def create_llm_provider(settings: Any) -> OpenAILLMProvider | AnthropicLLMProvider | OllamaLLMProvider | None:
    """Factory: create an LLM provider based on settings, or None if disabled."""
    provider = getattr(settings, "rag_llm_provider", "none")

    if provider == "none" or not getattr(settings, "rag_enabled", False):
        return None

    if provider == "openai":
        api_key = getattr(settings, "openai_api_key", "")
        model = getattr(settings, "rag_llm_model", "gpt-4o-mini")
        if not api_key:
            logger.warning("RAG: OpenAI provider selected but no API key configured")
            return None
        return OpenAILLMProvider(api_key=api_key, default_model=model)

    if provider == "anthropic":
        api_key = getattr(settings, "anthropic_api_key", "")
        model = getattr(settings, "rag_llm_model", "claude-sonnet-4-20250514")
        if not api_key:
            logger.warning("RAG: Anthropic provider selected but no API key configured")
            return None
        return AnthropicLLMProvider(api_key=api_key, default_model=model)

    if provider == "ollama":
        base_url = getattr(settings, "ollama_url", "http://localhost:11434")
        model = getattr(settings, "rag_llm_model", "llama3")
        return OllamaLLMProvider(base_url=base_url, default_model=model)

    logger.warning("RAG: Unknown LLM provider '%s', disabling", provider)
    return None
