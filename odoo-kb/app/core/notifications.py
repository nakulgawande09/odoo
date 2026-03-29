"""Triage notification dispatcher — sends alerts via WhatsApp/SMS.

After CRM analysis, notifies the sales team about new leads and
optionally sends a follow-up to the caller.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def notify_triage_result(
    messages_client: Any,
    analysis: Any,
    call_record: Any,
    team_numbers: list[str],
    notify_priorities: set[str],
) -> None:
    """Send triage notification to the sales team via WhatsApp.

    Args:
        messages_client: VonageMessagesClient instance.
        analysis: CRMAnalysis from the AI analyzer.
        call_record: The completed CallRecord.
        team_numbers: List of team WhatsApp numbers.
        notify_priorities: Set of priorities that trigger notifications (e.g. {"hot", "warm"}).
    """
    if not messages_client or not team_numbers:
        return

    if analysis.priority not in notify_priorities:
        logger.debug(
            "Skipping notification for call %s: priority %s not in %s",
            call_record.call_uuid,
            analysis.priority,
            notify_priorities,
        )
        return

    priority_emoji = {"hot": "HOT", "warm": "WARM", "cold": "COLD"}.get(
        analysis.priority, analysis.priority.upper()
    )

    products = ", ".join(analysis.product_interest) if analysis.product_interest else "N/A"
    caller = call_record.caller_number or "Unknown"

    message = (
        f"[{priority_emoji} Lead] New voice call\n\n"
        f"Caller: {caller}\n"
        f"Intent: {analysis.customer_intent}\n"
        f"Products: {products}\n"
        f"Sentiment: {analysis.sentiment}\n"
        f"Action: {analysis.suggested_next_action}\n\n"
        f"Summary: {analysis.summary}\n\n"
        f"Duration: {call_record.duration_seconds or 0}s"
    )

    for number in team_numbers:
        try:
            await messages_client.send_whatsapp(number, message)
            logger.info(
                "Triage notification sent to %s for call %s",
                number[:4] + "***" + number[-4:] if len(number) >= 7 else "***",
                call_record.call_uuid,
            )
        except Exception as e:
            logger.error(
                "Failed to send triage notification to %s: %s", number, e,
            )


async def send_caller_followup(
    messages_client: Any,
    analysis: Any,
    call_record: Any,
    channel: str = "whatsapp",
) -> None:
    """Send a follow-up message to the caller after the call.

    Args:
        messages_client: VonageMessagesClient instance.
        analysis: CRMAnalysis from the AI analyzer.
        call_record: The completed CallRecord.
        channel: "whatsapp" or "sms".
    """
    if not messages_client:
        return

    caller = call_record.caller_number
    if not caller:
        logger.warning("No caller number for follow-up on call %s", call_record.call_uuid)
        return

    # Build personalized follow-up
    contact_name = analysis.contact_info.get("name", "")
    greeting = f"Hi {contact_name}! " if contact_name else "Hi! "

    action_messages = {
        "send_quote": "We'll be sending you a personalized quote shortly.",
        "schedule_demo": "We'd love to schedule a demo for you. Reply with your preferred time.",
        "follow_up_call": "One of our team members will follow up with you soon.",
        "escalate": "We've escalated your request to our specialist team.",
        "resolve": "We hope we were able to help! Let us know if you need anything else.",
    }
    action_text = action_messages.get(
        analysis.suggested_next_action,
        "Let us know if you have any more questions.",
    )

    message = (
        f"{greeting}Thank you for calling!\n\n"
        f"{action_text}\n\n"
        f"You can reply to this message anytime with questions — "
        f"I'll search our knowledge base for answers."
    )

    try:
        if channel == "whatsapp":
            await messages_client.send_whatsapp(caller, message)
        else:
            await messages_client.send_sms(caller, message)

        masked = caller[:4] + "***" + caller[-4:] if len(caller) >= 7 else "***"
        logger.info(
            "Caller follow-up sent to %s via %s for call %s",
            masked,
            channel,
            call_record.call_uuid,
        )
    except Exception as e:
        logger.error(
            "Failed to send caller follow-up to %s: %s", caller, e,
        )
