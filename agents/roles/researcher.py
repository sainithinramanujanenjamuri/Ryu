"""Minimal Researcher Role implementation.

Specialized agent role decomposing goal fragments and generating research proposals
(ROADMAP Phase 5).
Per Correction 7, kept strictly minimal without expanding into general multi-agent runtime.

spec §7 (Cognitive Layer), ROADMAP Phase 5 — Phase 5
"""

from __future__ import annotations

from typing import Any

from agents.base import AgentProposal, BaseAgent
from core.orchestrator.goal_analyzer import GoalSpec


class ResearcherRole(BaseAgent):
    """Specialized researcher role decomposing goal spec fragments into research proposals."""

    def __init__(
        self,
        agent_id: str,
        space_id: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            space_id=space_id,
            model="researcher-v1",
            **kwargs,
        )

    def research_goal_fragment(
        self,
        goal_spec: GoalSpec,
        task_id: str,
    ) -> AgentProposal:
        """Decompose a goal spec fragment and generate a structured research proposal."""
        prompt = (
            f"Decompose research goal '{goal_spec.objective}' for task '{task_id}'. "
            f"Constraints: {goal_spec.constraints}. "
            f"Required capabilities: {goal_spec.required_capabilities}."
        )
        return self.step(
            task_id=task_id,
            instruction=prompt,
            correlation_id=f"corr-research-{goal_spec.goal_id}-{task_id}",
        )
