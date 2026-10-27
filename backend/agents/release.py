"""Narrative release assessment; decision authority stays in backend rules."""

from backend.agents.contracts import ReleaseExplanation
from backend.services.llm import complete

SYSTEM = """You are the Release Agent. Explain the supplied backend-computed
recommendation using only actual gate evidence, current-version approvals, checks,
traceability and unresolved findings. You must not change, override or soften the
computed recommendation. Not executed is not passed. Distinguish hackathon scope
from production readiness and identify unresolved limitations. No invented evidence.
Return explanation only; backend rules are the sole release-decision authority."""


async def run(evidence: dict, mode: str) -> dict:
    """Explain the authoritative computed release result."""
    if mode != "demo":
        return await complete(SYSTEM, {"evidence": evidence}, ReleaseExplanation)
    recommendation = evidence.get(
        "recommendation", evidence.get("report", {}).get("recommendation", "MANUAL REVIEW")
    )
    gates = evidence.get("gates", evidence.get("report", {}).get("gates", []))
    reasons = "; ".join(
        f"{gate.get('name')}: {gate.get('reason', gate.get('status', 'unknown'))}" for gate in gates
    )
    return ReleaseExplanation(
        explanation=f"Backend policy recommends {recommendation}. {reasons} This deterministic explanation reflects recorded evidence only. The contact reference uses simulated identity and volatile storage; readiness applies to the approved hackathon scope, not production deployment."
    ).model_dump()
