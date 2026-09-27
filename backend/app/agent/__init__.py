"""
CareLoop AI - LangGraph Agent Package (Phase 4)

A stateless, deterministic, document-grounded answer pipeline.

The graph is fixed and acyclic; the model does not choose its own next step,
tool, or retry.  It contributes answer text and chunk ids, and the code decides
what happens next.  See `app/agent/graph.py` for the full topology and
`app/agent/safety.py` for what the system does and does not guarantee.
"""
from app.agent.generation import GroundedResponseGenerator
from app.agent.graph import ALL_NODES, build_agent_graph
from app.agent.safety import AgentSafetyValidator, SafetyAssessment
from app.agent.service import AgentResult, AgentService
from app.agent.state import (
    AgentState,
    GroundedAnswer,
    GroundedSource,
    Route,
    SafetyFlagKind,
)

__all__ = [
    "ALL_NODES",
    "AgentResult",
    "AgentService",
    "AgentSafetyValidator",
    "AgentState",
    "GroundedAnswer",
    "GroundedResponseGenerator",
    "GroundedSource",
    "Route",
    "SafetyAssessment",
    "SafetyFlagKind",
    "build_agent_graph",
]
