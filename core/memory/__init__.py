"""Core Memory subsystem: adaptation layer and hint abstractions.

spec §4 (Space Memory), §7 (Adaptation Layer), ADR-0036 — Phase 10
"""

from __future__ import annotations

from core.memory.adaptation import AdaptationLayer, ExperienceHint

__all__ = ["AdaptationLayer", "ExperienceHint"]

