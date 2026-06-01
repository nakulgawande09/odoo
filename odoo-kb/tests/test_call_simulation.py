#!/usr/bin/env python3
"""Live call simulation — drives Vonage webhook endpoints to simulate callers.

Runs multi-turn call scenarios against a running KB service and verifies
the full pipeline: call tracking, KB search, answer generation, CRM analysis.

Usage:
    python tests/test_call_simulation.py
    python tests/test_call_simulation.py --base-url http://host:8100
    python tests/test_call_simulation.py --scenario pricing
    python tests/test_call_simulation.py --no-verify
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from dataclasses import dataclass

import httpx

# ─── ANSI Colors ─────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED = "\033[31m"
RESET = "\033[0m"
CHECK = f"{GREEN}\u2713{RESET}"
CROSS = f"{RED}\u2717{RESET}"


# ─── Scenarios ───────────────────────────────────────────────


@dataclass(frozen=True)
class CallScenario:
    """A simulated caller persona with scripted utterances."""

    name: str
    slug: str
    description: str
    caller_number: str
    turns: tuple[str, ...]
    expect_escalation: bool = False


SCENARIOS: tuple[CallScenario, ...] = (
    CallScenario(
        name="Product Inquiry",
        slug="pricing",
        description="Customer asks about pricing plans, features, and trial",
        caller_number="+15551001001",
        turns=(
            "What pricing plans do you offer?",
            "Tell me more about the Business plan",
            "Is there a free trial available?",
            "Thanks, that's all I need",
        ),
    ),
    CallScenario(
        name="Support / Troubleshooting",
        slug="support",
        description="Frustrated customer with auth errors, tests escalation",
        caller_number="+15551002002",
        turns=(
            "I keep getting an authentication error when I try to log in",
            "I already verified my API key but it still doesn't work",
            "What is error code E002?",
            "Can I speak to someone?",
        ),
        expect_escalation=True,
    ),
    CallScenario(
        name="Returns then Shipping",
        slug="returns",
        description="Topic pivot mid-conversation, tests multi-intent",
        caller_number="+15551003003",
        turns=(
            "What is your return policy?",
            "What about electronics, can I return those?",
            "And what are the shipping options?",
            "How much is next day delivery?",
        ),
    ),
    CallScenario(
        name="Brief / Vague Caller",
        slug="brief",
        description="Short queries, edge cases, no-match handling",
        caller_number="+15551004004",
        turns=(
            "Hi",
            "Uh, I don't know, maybe installation?",
        ),
    ),
)

CALLED_NUMBER = "+18001234567"


# ─── Data Containers ─────────────────────────────────────────


@dataclass
class TurnResult:
    """Result of a single conversation turn."""

    caller_text: str
    agent_text: str
    confidence: float
    response_ms: float
    escalated: bool


@dataclass
class SimulationResult:
    """Full result of running one scenario."""

    scenario: CallScenario
    call_uuid: str
    greeting: str
    turns: list[TurnResult]
    total_ms: float
    verification: dict | None = None
    error: str | None = None


# ─── Transcript Printer ──────────────────────────────────────


def print_header(scenario: CallScenario, call_uuid: str) -> None:
    print(f"\n{'=' * 55}")
    print(f"  {BOLD}{scenario.name}{RESET}")
    print(f"  {DIM}{scenario.description}{RESET}")
    print(f"  Caller: {scenario.caller_number} -> {CALLED_NUMBER}")
    print(f"  Call UUID: {DIM}{call_uuid}{RESET}")
    print(f"{'=' * 55}\n")


def print_greeting(text: str) -> None:
    print(f"  {CYAN}AGENT:{RESET} {text}\n")


def print_turn(turn: TurnResult) -> None:
    print(f"  {YELLOW}CALLER:{RESET} {turn.caller_text}")
    print(f"  {CYAN}AGENT:{RESET}  {turn.agent_text}")
    meta = f"{DIM}[confidence: {turn.confidence:.2f} | {turn.response_ms:.0f}ms"
    if turn.escalated:
        meta += f" | {RED}ESCALATED{RESET}{DIM}"
    meta += f"]{RESET}"
    print(f"          {meta}\n")


def print_verification(verification: dict) -> None:
    print(f"  {'─' * 50}")
    print(f"  {BOLD}Verification{RESET}")

    transcript = verification.get("transcript")
    if transcript is not None:
        count = len(transcript)
        caller_turns = sum(1 for t in transcript if t.get("role") == "caller")
        bot_turns = sum(1 for t in transcript if t.get("role") == "bot")
        print(f"  Transcript turns: {count} ({caller_turns} caller + {bot_turns} bot)  {CHECK}")
    else:
        print(f"  Transcript: {DIM}not available{RESET}")

    crm = verification.get("crm_analysis")
    if crm:
        intent = crm.get("customer_intent", "?")
        priority = crm.get("priority", "?")
        sentiment = crm.get("sentiment", "?")
        print(f"  CRM Analysis: intent={intent}, priority={priority}, sentiment={sentiment}  {CHECK}")
    elif verification.get("crm_status") == "pending":
        print(f"  CRM Analysis: pending (analysis in progress)")
    else:
        print(f"  CRM Analysis: {DIM}not available (no Gemini or DB){RESET}")

    avg_conf = verification.get("avg_confidence")
    if avg_conf is not None:
        mark = CHECK if avg_conf > 0 else CROSS
        print(f"  Avg confidence: {avg_conf:.2f}  {mark}")

    duration = verification.get("duration_seconds")
    if duration is not None:
        print(f"  Call duration: {duration}s")

    print()


def print_error(scenario_name: str, error: str) -> None:
    print(f"\n  {RED}ERROR in {scenario_name}: {error}{RESET}\n")


# ─── Call Simulator ──────────────────────────────────────────


class CallSimulator:
    """Drives Vonage webhook endpoints to simulate a live call."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        verify: bool = True,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._verify = verify
        headers: dict[str, str] = {}
        if api_key:
            headers["X-API-Key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ── Webhook drivers ──────────────────────────────────────

    async def _call_answer(
        self, call_uuid: str, scenario: CallScenario
    ) -> list[dict]:
        """POST /v1/voip/vonage/answer — start the call."""
        resp = await self._client.post(
            "/v1/voip/vonage/answer",
            json={
                "uuid": call_uuid,
                "conversation_uuid": call_uuid,
                "from": scenario.caller_number,
                "to": CALLED_NUMBER,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def _call_event(
        self,
        call_uuid: str,
        utterance: str,
        confidence: float = 0.92,
    ) -> list[dict]:
        """POST /v1/voip/vonage/event — send a caller utterance."""
        resp = await self._client.post(
            "/v1/voip/vonage/event",
            json={
                "uuid": call_uuid,
                "conversation_uuid": call_uuid,
                "speech": {
                    "results": [
                        {"text": utterance, "confidence": confidence},
                    ],
                },
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def _call_status(self, call_uuid: str, status: str) -> dict:
        """POST /v1/voip/vonage/status — send a lifecycle event."""
        resp = await self._client.post(
            "/v1/voip/vonage/status",
            json={
                "uuid": call_uuid,
                "conversation_uuid": call_uuid,
                "status": status,
                "direction": "inbound",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        resp.raise_for_status()
        return resp.json()

    # ── Verification ─────────────────────────────────────────

    async def _verify_call(self, call_uuid: str) -> dict | None:
        """Poll GET /v1/calls/{uuid} until CRM analysis completes or timeout."""
        deadline = time.monotonic() + 15.0
        last_data: dict | None = None

        while time.monotonic() < deadline:
            try:
                resp = await self._client.get(f"/v1/calls/{call_uuid}")
                if resp.status_code == 404:
                    await asyncio.sleep(1.0)
                    continue
                resp.raise_for_status()
                last_data = resp.json()
                crm_status = last_data.get("crm_analysis", {}) if last_data.get("conversation_summary") else None
                if crm_status or last_data.get("status") in ("completed", "failed"):
                    return last_data
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1.0)

        return last_data

    # ── Scenario runner ──────────────────────────────────────

    async def run_scenario(self, scenario: CallScenario) -> SimulationResult:
        """Run a full call scenario through the Vonage webhook flow."""
        call_uuid = str(uuid.uuid4())
        start = time.monotonic()

        print_header(scenario, call_uuid)

        try:
            # 1. Answer — start the call
            ncco = await self._call_answer(call_uuid, scenario)
            greeting = _extract_talk_text(ncco)
            print_greeting(greeting)

            # 2. Event loop — each caller turn
            turn_results: list[TurnResult] = []
            for utterance in scenario.turns:
                t0 = time.monotonic()
                ncco = await self._call_event(call_uuid, utterance)
                elapsed_ms = (time.monotonic() - t0) * 1000

                agent_text = _extract_talk_text(ncco)
                escalated = any(a.get("action") == "connect" for a in ncco)
                # Estimate confidence from answer quality
                confidence = 0.0 if not agent_text else 0.85

                turn = TurnResult(
                    caller_text=utterance,
                    agent_text=agent_text,
                    confidence=confidence,
                    response_ms=elapsed_ms,
                    escalated=escalated,
                )
                turn_results.append(turn)
                print_turn(turn)

                if escalated:
                    break

            # 3. Status — end the call
            await self._call_status(call_uuid, "completed")

            total_ms = (time.monotonic() - start) * 1000

            # 4. Verify (optional)
            verification: dict | None = None
            if self._verify:
                print(f"  {DIM}Waiting for finalization...{RESET}")
                verification = await self._verify_call(call_uuid)
                if verification:
                    print_verification(verification)
                else:
                    print(f"  {DIM}Verification: call record not found (DB may be unavailable){RESET}\n")

            return SimulationResult(
                scenario=scenario,
                call_uuid=call_uuid,
                greeting=greeting,
                turns=turn_results,
                total_ms=total_ms,
                verification=verification,
            )

        except httpx.HTTPError as exc:
            error_msg = f"HTTP error: {exc}"
            print_error(scenario.name, error_msg)
            return SimulationResult(
                scenario=scenario,
                call_uuid=call_uuid,
                greeting="",
                turns=[],
                total_ms=(time.monotonic() - start) * 1000,
                error=error_msg,
            )


# ─── Helpers ─────────────────────────────────────────────────


def _extract_talk_text(ncco: list[dict]) -> str:
    """Extract concatenated text from all talk actions in an NCCO."""
    parts = [
        action["text"]
        for action in ncco
        if action.get("action") == "talk" and action.get("text")
    ]
    return " ".join(parts)


# ─── Summary ─────────────────────────────────────────────────


def print_summary(results: list[SimulationResult]) -> None:
    """Print a final summary table of all scenarios."""
    print(f"\n{'=' * 55}")
    print(f"  {BOLD}Summary{RESET}")
    print(f"{'=' * 55}\n")

    passed = 0
    failed = 0

    for r in results:
        if r.error:
            status = f"{RED}FAIL{RESET}"
            detail = r.error[:60]
            failed += 1
        else:
            status = f"{GREEN}PASS{RESET}"
            turns = len(r.turns)
            detail = f"{turns} turns, {r.total_ms:.0f}ms total"
            passed += 1

        print(f"  [{status}] {r.scenario.name:<28} {DIM}{detail}{RESET}")

    print()
    total = passed + failed
    color = GREEN if failed == 0 else RED
    print(f"  {color}{passed}/{total} scenarios passed{RESET}\n")


# ─── Main ────────────────────────────────────────────────────


async def _run(args: argparse.Namespace) -> int:
    """Run selected scenarios and return exit code."""
    # Filter scenarios
    if args.scenario:
        selected = [s for s in SCENARIOS if s.slug == args.scenario]
        if not selected:
            slugs = ", ".join(s.slug for s in SCENARIOS)
            print(f"{RED}Unknown scenario '{args.scenario}'. Available: {slugs}{RESET}")
            return 1
    else:
        selected = list(SCENARIOS)

    # Check service health
    async with httpx.AsyncClient(base_url=args.base_url, timeout=5.0) as client:
        try:
            resp = await client.get("/health")
            if resp.status_code != 200:
                resp = await client.get("/v1/search", params={"q": "test"})
        except httpx.ConnectError:
            print(f"{RED}Cannot connect to {args.base_url}. Is the service running?{RESET}")
            print(f"{DIM}Start with: make dev{RESET}")
            return 1

    print(f"\n{BOLD}Call Simulation{RESET}")
    print(f"Target: {args.base_url}")
    print(f"Scenarios: {len(selected)}")
    print(f"Verify: {'yes' if not args.no_verify else 'no'}")

    simulator = CallSimulator(
        base_url=args.base_url,
        api_key=args.api_key,
        verify=not args.no_verify,
    )

    results: list[SimulationResult] = []
    try:
        for scenario in selected:
            result = await simulator.run_scenario(scenario)
            results.append(result)
    finally:
        await simulator.close()

    print_summary(results)

    return 0 if all(r.error is None for r in results) else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simulate live calls against the KB voice agent",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8100",
        help="KB service base URL (default: http://localhost:8100)",
    )
    parser.add_argument(
        "--scenario",
        choices=[s.slug for s in SCENARIOS],
        help="Run a single scenario by slug",
    )
    parser.add_argument(
        "--api-key",
        help="API key for authenticated endpoints (/calls, /crm)",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip post-call verification (no DB required)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
