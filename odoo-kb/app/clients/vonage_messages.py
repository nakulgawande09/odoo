"""Vonage Messages API client — sends WhatsApp and SMS messages.

Uses the Vonage Messages API v1 to send outbound messages.
Requires a Vonage Application with Messages capability enabled
and JWT authentication via the application's private key.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

logger = logging.getLogger(__name__)

VONAGE_MESSAGES_URL = "https://api.nexmo.com/v1/messages"


@dataclass(frozen=True)
class VonageMessagesConfig:
    """Immutable config for Vonage Messages API."""

    application_id: str
    private_key_path: str
    whatsapp_number: str = ""
    sms_from: str = ""
    api_key: str = ""
    api_secret: str = ""


def _generate_jwt(application_id: str, private_key: str) -> str:
    """Generate a short-lived JWT for Vonage API authentication."""
    now = int(time.time())
    payload = {
        "application_id": application_id,
        "iat": now,
        "exp": now + 900,  # 15 minutes
        "jti": f"{application_id}-{now}",
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


class VonageMessagesClient:
    """Send WhatsApp and SMS messages via Vonage Messages API."""

    def __init__(self, config: VonageMessagesConfig) -> None:
        self._config = config
        self._private_key: str | None = None

    def _load_private_key(self) -> str:
        """Load and cache the private key from file."""
        if self._private_key is None:
            with open(self._config.private_key_path) as f:
                self._private_key = f.read()
        return self._private_key

    def _get_auth_header(self) -> dict[str, str]:
        """Build JWT Authorization header."""
        key = self._load_private_key()
        token = _generate_jwt(self._config.application_id, key)
        return {"Authorization": f"Bearer {token}"}

    async def send_whatsapp(
        self,
        to: str,
        text: str,
        from_number: str | None = None,
    ) -> dict:
        """Send a WhatsApp text message.

        Args:
            to: Recipient phone number (E.164 format, no +).
            text: Message body.
            from_number: Override sender (defaults to config).

        Returns:
            Vonage API response dict with message_uuid.
        """
        sender = from_number or self._config.whatsapp_number
        if not sender:
            raise ValueError("No WhatsApp sender number configured")

        payload = {
            "message_type": "text",
            "text": text,
            "to": to.lstrip("+"),
            "from": sender.lstrip("+"),
            "channel": "whatsapp",
        }
        return await self._send(payload)

    async def send_sms(
        self,
        to: str,
        text: str,
        from_number: str | None = None,
    ) -> dict:
        """Send an SMS message.

        Args:
            to: Recipient phone number (E.164 format).
            text: Message body (max 1600 chars for concatenated SMS).
            from_number: Override sender (defaults to config).

        Returns:
            Vonage API response dict with message_uuid.
        """
        sender = from_number or self._config.sms_from
        if not sender:
            raise ValueError("No SMS sender number configured")

        payload = {
            "message_type": "text",
            "text": text[:1600],
            "to": to.lstrip("+"),
            "from": sender.lstrip("+"),
            "channel": "sms",
        }
        return await self._send(payload)

    async def send_whatsapp_template(
        self,
        to: str,
        template_name: str,
        parameters: list[str],
        namespace: str = "",
        language_code: str = "en",
    ) -> dict:
        """Send a WhatsApp template message (for initiating conversations).

        WhatsApp requires pre-approved templates for business-initiated messages.

        Args:
            to: Recipient phone number.
            template_name: Approved template name.
            parameters: Template parameter values.
            namespace: Template namespace (from WhatsApp Business).
            language_code: Template language.

        Returns:
            Vonage API response dict.
        """
        sender = self._config.whatsapp_number
        if not sender:
            raise ValueError("No WhatsApp sender number configured")

        whatsapp_custom = {
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": p} for p in parameters
                        ],
                    }
                ],
            },
        }
        if namespace:
            whatsapp_custom["template"]["namespace"] = namespace

        payload = {
            "message_type": "custom",
            "to": to.lstrip("+"),
            "from": sender.lstrip("+"),
            "channel": "whatsapp",
            "custom": whatsapp_custom,
        }
        return await self._send(payload)

    async def _send(self, payload: dict) -> dict:
        """Send a message via the Vonage Messages API."""
        headers = {
            **self._get_auth_header(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                VONAGE_MESSAGES_URL,
                json=payload,
                headers=headers,
            )

        if response.status_code in (200, 202):
            data = response.json()
            logger.info(
                "Message sent: channel=%s, to=%s, uuid=%s",
                payload.get("channel"),
                payload.get("to"),
                data.get("message_uuid", ""),
            )
            return data

        logger.error(
            "Vonage Messages API error: status=%d, body=%s",
            response.status_code,
            response.text[:200],
        )
        return {
            "error": True,
            "status_code": response.status_code,
            "detail": response.text[:500],
        }


def create_vonage_messages_client(settings: Any) -> VonageMessagesClient | None:
    """Factory: returns Vonage Messages client if configured."""
    app_id = getattr(settings, "vonage_application_id", "")
    key_path = getattr(settings, "vonage_private_key_path", "")

    if not app_id or not key_path:
        logger.info("Vonage Messages client disabled (no application_id or private_key)")
        return None

    config = VonageMessagesConfig(
        application_id=app_id,
        private_key_path=key_path,
        whatsapp_number=getattr(settings, "vonage_whatsapp_number", ""),
        sms_from=getattr(settings, "vonage_sms_from", ""),
        api_key=getattr(settings, "vonage_api_key", ""),
        api_secret=getattr(settings, "vonage_api_secret", ""),
    )
    return VonageMessagesClient(config)
