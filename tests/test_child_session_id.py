"""#94: session reset bookkeeping belongs to the parent, even when helpers share its state."""
from unittest.mock import Mock

import pytest

from test_delegated_child import child
from test_levers import approve_gate, make_plugin, write_plan
from test_plan_mode import session_env


STATE_KEY = "sk:unit-session"
PARENT_SID = "parent-sid"
NEXT_SID = "child-sid"


@pytest.fixture
def planning_parent(make_plugin):
    plugin = make_plugin(plan_profile="classic")
    plugin.command("on")
    assert plugin.pre_tool_call("read_file", {}, session_id=PARENT_SID) is None
    assert plugin._load_state(STATE_KEY)["session_id_hash"] == plugin._session_id_hash(PARENT_SID)
    return plugin


@pytest.mark.parametrize("tool,args", [
    pytest.param("terminal", {"command": "touch output.txt"}, id="blocked-terminal"),
    pytest.param("read_file", {}, id="allowed-read-file"),
])
@pytest.mark.parametrize("delegated", [True, False], ids=["child", "parent"])
def test_tool_call_session_hash(planning_parent, child, tool, args, delegated):
    plugin = planning_parent
    expected = plugin.pre_tool_call(tool, args, session_id=PARENT_SID)
    if tool == "terminal":
        assert expected["action"] == "block"
    else:
        assert expected is None

    child["on"] = delegated
    assert plugin.pre_tool_call(tool, args, session_id=NEXT_SID) == expected
    assert plugin._load_state(STATE_KEY)["session_id_hash"] == plugin._session_id_hash(
        PARENT_SID if delegated else NEXT_SID
    )


@pytest.mark.parametrize("phase", ["planning", "executing"])
@pytest.mark.parametrize("delegated", [True, False], ids=["child", "parent"])
def test_llm_call_session_hash(planning_parent, child, monkeypatch, phase, delegated):
    plugin = planning_parent
    if phase == "executing":
        assert approve_gate(plugin, write_plan(plugin))["approved"]
        assert plugin._load_state(STATE_KEY)["phase"] == "executing"
    expected = plugin.pre_llm_call(platform="cli", session_id=PARENT_SID)
    assert expected and "context" in expected
    assert plugin._load_state(STATE_KEY)["session_id_hash"] == plugin._session_id_hash(PARENT_SID)
    mark_session = Mock(wraps=plugin._mark_session)
    monkeypatch.setattr(plugin, "_mark_session", mark_session)

    child["on"] = delegated
    assert plugin.pre_llm_call(platform="cli", session_id=NEXT_SID) == expected
    assert plugin._load_state(STATE_KEY)["session_id_hash"] == plugin._session_id_hash(
        PARENT_SID if delegated else NEXT_SID
    )
    if delegated:
        mark_session.assert_not_called()
    else:
        mark_session.assert_called_once()


def test_unbound_reset_finds_parent_after_child_call(planning_parent, child, session_env):
    plugin = planning_parent
    child["on"] = True
    assert plugin.pre_tool_call("read_file", {}, session_id=NEXT_SID) is None
    child["on"] = False

    session_env.update(HERMES_SESSION_KEY="", HERMES_SESSION_PLATFORM="telegram")
    plugin.on_session_reset(platform="telegram", old_session_id=PARENT_SID)

    assert not plugin._load_state(STATE_KEY).get("active")
