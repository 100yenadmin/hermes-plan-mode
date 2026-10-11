"""Isolate the process-wide plan_once display policy between tests."""
import pytest

from plan_mode import plugin as pm


@pytest.fixture(autouse=True)
def reset_plan_once_visibility():
    pm._reset_plan_once_visibility()
    yield
    pm._reset_plan_once_visibility()
