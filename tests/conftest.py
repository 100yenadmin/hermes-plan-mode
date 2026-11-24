"""Test isolation: a throwaway HOME and HERMES_HOME for the whole run, and the plan_once display policy per test."""
import atexit
import os
import shutil
import tempfile

# Before any test module imports Hermes: importing it loads the home it sees (its .env included) and creates
# missing folders there. On a developer machine the real ~/.hermes is a live agent's home (#107).
_TEST_HOME = tempfile.mkdtemp(prefix="plan-mode-tests-home-")
os.environ["HOME"] = _TEST_HOME
os.environ["USERPROFILE"] = _TEST_HOME
os.environ["HERMES_HOME"] = os.path.join(_TEST_HOME, ".hermes")
atexit.register(shutil.rmtree, _TEST_HOME, True)

import pytest  # noqa: E402

from plan_mode import plugin as pm  # noqa: E402


@pytest.fixture
def symlink_or_skip():
    def create(link, target, *, target_is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks need a privilege on this platform")

    return create


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
