"""Public registration surface for the plan-mode plugin."""

from .plugin import PlanModePlugin


def register(ctx) -> None:
    """Register plan-mode's command and enforcement hooks."""
    PlanModePlugin(ctx).register()


__all__ = ["PlanModePlugin", "register"]
