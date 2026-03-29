"""AI-powered conversation analyzer for CRM opportunity creation.

Analyzes call transcripts using Gemini to extract structured data:
customer intent, sentiment, priority, product interest, and suggested
next actions. The output drives automatic CRM lead creation in Odoo.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Prompt template for Gemini transcript analysis
_ANALYSIS_PROMPT = """\
You are an AI sales analyst. Analyze this phone call transcript and extract
structured information for CRM lead creation.

Transcript:
{transcript}

Caller number: {caller_number}
Call duration: {duration_seconds} seconds
Agent: {agent_id}

Return a JSON object with exactly these fields:
{{
  "summary": "2-3 sentence summary of the conversation",
  "customer_intent": "purchase" | "support" | "inquiry" | "complaint" | "return" | "other",
  "product_interest": ["list of products or services mentioned"],
  "urgency": "high" | "medium" | "low",
  "priority": "hot" | "warm" | "cold",
  "sentiment": "positive" | "neutral" | "negative",
  "contact_info": {{"name": "...", "email": "...", "company": "..."}},
  "suggested_next_action": "follow_up_call" | "send_quote" | "schedule_demo" | "escalate" | "resolve" | "no_action",
  "tags": ["auto-generated tags for CRM"],
  "confidence": 0.0 to 1.0
}}

Rules:
- If a field cannot be determined, use null or empty values
- "hot" priority = ready to buy or urgent issue
- "warm" priority = interested but not immediate
- "cold" priority = general inquiry, low engagement
- confidence reflects how certain you are about the overall analysis
- tags should capture the topic and nature of the conversation

Return ONLY valid JSON, no markdown or explanation.
"""


@dataclass(frozen=True)
class CRMAnalysis:
    """Structured output from AI transcript analysis."""

    summary: str = ""
    customer_intent: str = "inquiry"
    product_interest: tuple[str, ...] = ()
    urgency: str = "medium"
    priority: str = "warm"
    sentiment: str = "neutral"
    contact_info: dict[str, str] = field(default_factory=dict)
    suggested_next_action: str = "no_action"
    tags: tuple[str, ...] = ()
    confidence: float = 0.0


def analysis_to_dict(analysis: CRMAnalysis) -> dict[str, Any]:
    """Convert a frozen CRMAnalysis to a JSON-serializable dict."""
    return {
        "summary": analysis.summary,
        "customer_intent": analysis.customer_intent,
        "product_interest": list(analysis.product_interest),
        "urgency": analysis.urgency,
        "priority": analysis.priority,
        "sentiment": analysis.sentiment,
        "contact_info": dict(analysis.contact_info),
        "suggested_next_action": analysis.suggested_next_action,
        "tags": list(analysis.tags),
        "confidence": analysis.confidence,
    }


def _parse_analysis(raw: dict[str, Any]) -> CRMAnalysis:
    """Parse raw JSON response into an immutable CRMAnalysis."""
    valid_intents = {"purchase", "support", "inquiry", "complaint", "return", "other"}
    valid_urgency = {"high", "medium", "low"}
    valid_priority = {"hot", "warm", "cold"}
    valid_sentiment = {"positive", "neutral", "negative"}
    valid_actions = {
        "follow_up_call", "send_quote", "schedule_demo",
        "escalate", "resolve", "no_action",
    }

    intent = raw.get("customer_intent", "inquiry")
    urgency = raw.get("urgency", "medium")
    priority = raw.get("priority", "warm")
    sentiment = raw.get("sentiment", "neutral")
    action = raw.get("suggested_next_action", "no_action")

    return CRMAnalysis(
        summary=str(raw.get("summary", "")),
        customer_intent=intent if intent in valid_intents else "inquiry",
        product_interest=tuple(raw.get("product_interest", []) or []),
        urgency=urgency if urgency in valid_urgency else "medium",
        priority=priority if priority in valid_priority else "warm",
        sentiment=sentiment if sentiment in valid_sentiment else "neutral",
        contact_info=raw.get("contact_info") or {},
        suggested_next_action=action if action in valid_actions else "no_action",
        tags=tuple(raw.get("tags", []) or []),
        confidence=float(raw.get("confidence", 0.0)),
    )


def _format_transcript(turns: list[dict]) -> str:
    """Format transcript turns into readable text."""
    lines = []
    for turn in turns:
        role = turn.get("role", "unknown").upper()
        text = turn.get("text", "")
        lines.append(f"{role}: {text}")
    return "\n".join(lines)


class CRMAnalyzer:
    """Analyzes call transcripts using Gemini for CRM data extraction."""

    def __init__(self, settings: Any) -> None:
        self._api_key = settings.gemini_api_key
        self._model = getattr(settings, "crm_analysis_model", "gemini-2.5-flash")
        self._timeout = getattr(settings, "crm_analysis_timeout", 15.0)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
                self._client = genai.Client(api_key=self._api_key)
            except ImportError:
                raise RuntimeError(
                    "google-genai package required. Install with: "
                    "pip install google-genai"
                )
        return self._client

    async def analyze(
        self,
        transcript: list[dict],
        caller_number: str = "",
        duration_seconds: int = 0,
        agent_id: int | None = None,
    ) -> CRMAnalysis:
        """Analyze a call transcript and return structured CRM data.

        Returns a default CRMAnalysis on error (never raises).
        """
        if not transcript:
            logger.warning("Cannot analyze empty transcript")
            return CRMAnalysis()

        formatted = _format_transcript(transcript)
        prompt = _ANALYSIS_PROMPT.format(
            transcript=formatted,
            caller_number=caller_number or "unknown",
            duration_seconds=duration_seconds or 0,
            agent_id=agent_id or "none",
        )

        try:
            result = await asyncio.wait_for(
                self._call_gemini(prompt),
                timeout=self._timeout,
            )
            return result
        except asyncio.TimeoutError:
            logger.error("CRM analysis timed out after %.1fs", self._timeout)
            return CRMAnalysis(summary="Analysis timed out")
        except Exception as e:
            logger.error("CRM analysis failed: %s", e)
            return CRMAnalysis(summary=f"Analysis failed: {e}")

    async def _call_gemini(self, prompt: str) -> CRMAnalysis:
        """Call Gemini API and parse the structured response."""
        client = self._get_client()

        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=self._model,
                contents=prompt,
            ),
        )

        text = response.text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()
        if text.startswith("json"):
            text = text[4:].strip()

        raw = json.loads(text)
        return _parse_analysis(raw)


def create_crm_analyzer(settings: Any) -> CRMAnalyzer | None:
    """Factory: returns CRM analyzer if Gemini is configured."""
    if not getattr(settings, "gemini_api_key", ""):
        logger.info("CRM analyzer disabled (no gemini_api_key)")
        return None
    if not getattr(settings, "crm_auto_create", True):
        logger.info("CRM analyzer disabled (crm_auto_create=false)")
        return None
    return CRMAnalyzer(settings)
