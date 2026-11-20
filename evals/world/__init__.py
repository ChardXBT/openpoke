"""Synthetic worlds and routing scenarios for the agent-overload evaluation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from evals.world.generate import build_world
    from evals.world.scenarios import build_scenarios

__all__ = ["build_scenarios", "build_world"]


def __getattr__(name: str) -> Any:
    # Lazy so `python -m evals.world.generate` does not import the module twice.
    if name == "build_world":
        from evals.world.generate import build_world

        return build_world
    if name == "build_scenarios":
        from evals.world.scenarios import build_scenarios

        return build_scenarios
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
