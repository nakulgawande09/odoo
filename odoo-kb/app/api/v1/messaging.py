"""WhatsApp & SMS messaging webhooks via Vonage Messages API.

Handles inbound messages (WhatsApp/SMS) by searching the KB and
replying via the same channel. Also handles delivery status updates.

Endpoints:
  POST /v1/messaging/inbound  – Inbound message (WhatsApp or SMS)
  POST /v1/messaging/status   – Message delivery status updates
  GET  /v1/messaging/log      – Message history (paginated)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, func, select

from app.dependencies import (
    get_orchestrator,
    get_query_logger,
    get_session_factory,
    get_vonage_messages_client,
)
from app.schemas.search import SearchRequest

logger = logging.getLogger(__name__)

router = APIRouter()


def _mask_phone(number: str) -> str:
    """Mask a phone number for safe logging, e.g. +1555***4567."""
    if not number or len(number) < 7:
        return "***"
    return number[:4] + "***" + number[-4:]


@router.post("/messaging/inbound")
async def messaging_inbound(
    request: Request,
    orchestrator=Depends(get_orchestrator),
) -> dict:
    """Vonage inbound message webhook — WhatsApp or SMS.

    Receives a message, searches the KB, and replies via the same channel.
    """
    body = await request.json()

    channel = body.get("channel", "sms")
    sender = body.get("from", "")
    recipient = body.get("to", "")
    text = body.get("text", "")
    message_uuid = body.get("message_uuid", "")

    logger.info(
        "Inbound %s from %s: (message received)",
        channel,
        _mask_phone(sender),
    )

    if not text.strip():
        return {"status": "ok", "action": "ignored", "reason": "empty message"}

    # Use sender number as conversation ID for context continuity
    conversation_id = f"msg-{sender}"

    # Log inbound message
    session_factory = get_session_factory()
    if session_factory:
        asyncio.create_task(
            _log_message(
                session_factory,
                message_uuid=message_uuid,
                channel=channel,
                direction="inbound",
                sender=sender,
                recipient=recipient,
                text=text,
                conversation_id=conversation_id,
            )
        )

    # Search KB
    start = time.monotonic()
    search_request = SearchRequest(
        query=text,
        limit=3,
        source=f"messaging_{channel}",
        conversation_id=conversation_id,
    )
    search_response = await orchestrator.search(search_request)
    elapsed_ms = (time.monotonic() - start) * 1000

    # Build reply
    reply = _build_reply(search_response.results, channel)

    logger.info(
        "Messaging reply: channel=%s, to=%s, results=%d, time=%.1fms",
        channel,
        _mask_phone(sender),
        len(search_response.results),
        elapsed_ms,
    )

    # Send reply via Vonage Messages API
    messages_client = get_vonage_messages_client()
    reply_uuid = None
    if messages_client:
        asyncio.create_task(
            _send_reply(messages_client, channel, sender, reply, session_factory, conversation_id)
        )
    else:
        logger.warning("No Vonage Messages client — reply not sent (client disabled)")

    # Log query
    query_logger = get_query_logger()
    if query_logger:
        asyncio.create_task(
            query_logger.log(
                query=text,
                processed_query={
                    "source": f"messaging_{channel}",
                    "sender": sender,
                },
                results_count=len(search_response.results),
                source=f"messaging_{channel}",
                conversation_id=conversation_id,
                search_time_ms=elapsed_ms,
            )
        )

    return {
        "status": "ok",
        "channel": channel,
        "reply": reply,
        "results_count": len(search_response.results),
    }


@router.post("/messaging/status")
async def messaging_status(request: Request) -> dict:
    """Vonage message delivery status webhook.

    Receives: submitted, delivered, read, rejected, undeliverable.
    """
    body = await request.json()

    message_uuid = body.get("message_uuid", "")
    status = body.get("status", "")
    channel = body.get("channel", "")

    logger.info(
        "Message status: uuid=%s, status=%s, channel=%s",
        message_uuid,
        status,
        channel,
    )

    # Update message log status
    session_factory = get_session_factory()
    if session_factory and message_uuid:
        asyncio.create_task(
            _update_message_status(session_factory, message_uuid, status)
        )

    return {"status": "ok"}


@router.get("/messaging/log")
async def messaging_log(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    channel: str | None = Query(None),
    sender: str | None = Query(None),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Retrieve message history with optional filters."""
    from app.models.message_log import MessageLog

    if not session_factory:
        return {"total": 0, "offset": offset, "limit": limit, "messages": []}

    async with session_factory() as session:
        query = select(MessageLog).order_by(desc(MessageLog.created_at))

        if channel:
            query = query.where(MessageLog.channel == channel)
        if sender:
            safe_sender = sender.replace("%", r"\%").replace("_", r"\_")
            query = query.where(
                (MessageLog.sender.ilike(f"%{safe_sender}%"))
                | (MessageLog.recipient.ilike(f"%{safe_sender}%"))
            )

        # Count total
        count_q = select(func.count(MessageLog.id))
        if channel:
            count_q = count_q.where(MessageLog.channel == channel)
        if sender:
            safe_sender = sender.replace("%", r"\%").replace("_", r"\_")
            count_q = count_q.where(
                (MessageLog.sender.ilike(f"%{safe_sender}%"))
                | (MessageLog.recipient.ilike(f"%{safe_sender}%"))
            )
        total = (await session.execute(count_q)).scalar() or 0

        # Fetch page
        result = await session.execute(query.offset(offset).limit(limit))
        messages = result.scalars().all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "messages": [
            {
                "id": m.id,
                "message_uuid": m.message_uuid,
                "channel": m.channel,
                "direction": m.direction,
                "sender": m.sender,
                "recipient": m.recipient,
                "text": m.text,
                "status": m.status,
                "conversation_id": m.conversation_id,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
    }


# ─── Helpers ──────────────────────────────────────────────────


def _build_reply(results: list, channel: str) -> str:
    """Build a text reply from search results, adapted for the channel."""
    if not results:
        return (
            "I couldn't find an answer to your question. "
            "Please try rephrasing or call us for assistance."
        )

    top = results[0]
    confidence = top.relevance_score
    content = top.content or top.snippet

    if confidence < 0.3:
        return (
            "I'm not confident in my answer. "
            "Could you rephrase your question or call us for help?"
        )

    # WhatsApp supports longer messages; SMS should be concise
    max_len = 1500 if channel == "whatsapp" else 450
    if len(content) > max_len:
        truncated = content[:max_len]
        last_period = truncated.rfind(".")
        if last_period > max_len // 2:
            content = truncated[: last_period + 1]
        else:
            content = truncated + "..."

    return content


async def _send_reply(
    client, channel: str, to: str, text: str,
    session_factory, conversation_id: str,
) -> None:
    """Send reply and log the outbound message."""
    try:
        if channel == "whatsapp":
            result = await client.send_whatsapp(to, text)
        else:
            result = await client.send_sms(to, text)

        reply_uuid = result.get("message_uuid", "")

        if session_factory:
            await _log_message(
                session_factory,
                message_uuid=reply_uuid,
                channel=channel,
                direction="outbound",
                sender="bot",
                recipient=to,
                text=text,
                status="sent",
                conversation_id=conversation_id,
            )
    except Exception as e:
        logger.error("Failed to send %s reply to %s: %s", channel, to, e)


async def _log_message(
    session_factory,
    message_uuid: str = "",
    channel: str = "",
    direction: str = "",
    sender: str = "",
    recipient: str = "",
    text: str = "",
    status: str = "received",
    conversation_id: str = "",
) -> None:
    """Insert a message into the log table."""
    from app.models.message_log import MessageLog

    try:
        async with session_factory() as session:
            msg = MessageLog(
                id=str(uuid.uuid4()),
                message_uuid=message_uuid,
                channel=channel,
                direction=direction,
                sender=sender,
                recipient=recipient,
                text=text,
                status=status,
                conversation_id=conversation_id,
            )
            session.add(msg)
            await session.commit()
    except Exception as e:
        logger.error("Failed to log message: %s", e)


async def _update_message_status(
    session_factory, message_uuid: str, status: str,
) -> None:
    """Update a message's delivery status."""
    from sqlalchemy import update
    from app.models.message_log import MessageLog

    try:
        async with session_factory() as session:
            await session.execute(
                update(MessageLog)
                .where(MessageLog.message_uuid == message_uuid)
                .values(status=status)
            )
            await session.commit()
    except Exception as e:
        logger.error("Failed to update message status: %s", e)
