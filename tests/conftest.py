"""Isolate the process-wide plan_once display policy between tests."""
import pytest

from plan_mode import plugin as pm


@pytest.fixture(autouse=True)
def reset_plan_once_visibility():
    pm._reset_plan_once_visibility()
    yield
    pm._reset_plan_once_visibility()


@pytest.fixture
def directive_lean(monkeypatch):
    """Lean without approval_card: the plan-approval directive path.

    approval_card joined the lean defaults in 0.6.0. The directive path remains for the classic profile, custom lever
    sets without the card, and surfaces without a card, so the modules that cover it keep testing it here; the
    default-lean card path is covered in test_approval_card.py.
    """
    monkeypatch.setattr(pm, "LEAN_LEVERS", pm.LEAN_LEVERS - {"approval_card"})
