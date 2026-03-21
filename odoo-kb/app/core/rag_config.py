"""RAG (Retrieval-Augmented Generation) configuration."""
from __future__ import annotations

from dataclasses import dataclass


VOIP_SYSTEM_PROMPT = (
    "You are a helpful assistant answering questions over the phone.\n"
    "Rules:\n"
    "- Use clear, conversational sentences only\n"
    "- No markdown, no bullet points, no numbered lists, no special characters\n"
    "- Keep the answer to 2-4 sentences maximum\n"
    "- If the context does not contain the answer, say "
    '"I don\'t have that information in our knowledge base"\n'
    "- Do not make up information beyond what is in the context\n"
    '- Avoid abbreviations that sound awkward when spoken (use "for example" not "e.g.")'
)

SEARCH_SYSTEM_PROMPT = (
    "You are a knowledge base assistant. Synthesize the provided context "
    "passages into a clear, accurate answer. You may reference passage numbers. "
    "Only use information from the provided context. If the context is "
    "insufficient, say so clearly."
)

LIVECHAT_SYSTEM_PROMPT = (
    "You are a helpful support assistant. Give a concise, friendly answer based on "
    "the provided context. You may use simple formatting like bold for emphasis. "
    "Keep it under 3 sentences."
)

CHANNEL_PROMPTS = {
    "voip": VOIP_SYSTEM_PROMPT,
    "search": SEARCH_SYSTEM_PROMPT,
    "livechat": LIVECHAT_SYSTEM_PROMPT,
    "whatsapp": LIVECHAT_SYSTEM_PROMPT,
}


@dataclass
class RAGConfig:
    """Configuration for RAG answer synthesis."""

    enabled: bool = False
    llm_provider: str = "openai"       # "openai" | "anthropic" | "ollama" | "none"
    llm_model: str = "gpt-4o-mini"
    system_prompt: str = ""            # Empty = use channel-appropriate default
    max_tokens: int = 300
    temperature: float = 0.3
    max_context_chunks: int = 5
    max_context_tokens: int = 3000
    channel: str = "voip"              # "voip" | "search" | "livechat" | "whatsapp"

    def get_system_prompt(self) -> str:
        """Return the effective system prompt (custom or channel default)."""
        if self.system_prompt:
            return self.system_prompt
        return CHANNEL_PROMPTS.get(self.channel, SEARCH_SYSTEM_PROMPT)
