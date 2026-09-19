"""RYU AI Cognitive Agents Layer.

Implements deterministic state machines, context scopes, and specialized roles
(docs/Architecture §7, §12).
"""

from __future__ import annotations

from agents.base import AgentProposal, AgentState, BaseAgent, ProposalValidator
from agents.context import ContextEntry, ContextManager, ContextScope, HandoffNote
from agents.roles.researcher import ResearcherRole

__all__ = [
    "AgentProposal",
    "AgentState",
    "BaseAgent",
    "ContextEntry",
    "ContextManager",
    "ContextScope",
    "HandoffNote",
    "ProposalValidator",
    "ResearcherRole",
]
