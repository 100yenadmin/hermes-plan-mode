"""Hermes plan-mode plugin entry point."""

if __package__:  # Hermes loads the plugin root as a package.
    from .plan_mode import register
else:  # Pytest may collect a root __init__.py as a top-level module.
    from plan_mode import register

__all__ = ["register"]
