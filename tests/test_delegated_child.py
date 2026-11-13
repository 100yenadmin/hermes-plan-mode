"""#92: a delegate_task child shares its parent's session key, so it must never change the parent's plan mode."""
import json

import pytest

from plan_mode import plugin as pm
from plan_mode import render
from test_levers import make_plugin, write_plan
from test_plan_mode import session_env


@pytest.fixture
def child(monkeypatch):
    """Flip the delegated-child context on and off, as Hermes does around a child's run."""
    flag = {"on": False}
    monkeypatch.setattr(pm, "_delegated_child", lambda: flag["on"])
    return flag


def _state(plugin):
    return {key: dict(value) if isinstance(value, dict) else value for key, value in plugin.ctx.state.values.items()}


def _tool(plugin, **args):
    return json.loads(plugin.tool(args))["message"]


@pytest.mark.parametrize("profile", ["classic", "lean"])
def test_child_on_leaves_parent_and_siblings_unlocked(make_plugin, child, profile):
    plugin = make_plugin(plan_profile=profile)
    before = _state(plugin)
    child["on"] = True
    reply = _tool(plugin, action="on", reason="import the data")
    assert reply == pm._CHILD_REFUSAL
    assert _state(plugin) == before
    assert plugin.pre_tool_call("terminal", {"command": "ls"}) is None  # a sibling child is not blocked
    child["on"] = False
    assert plugin.pre_tool_call("terminal", {"command": "ls"}) is None  # nor is the parent
    assert _tool(plugin, action="status").startswith("Plan mode: off\n")


@pytest.mark.parametrize("entered", ["agent", "user"])
def test_child_off_cannot_end_the_parents_plan_mode(make_plugin, child, entered):
    plugin = make_plugin()
    if entered == "agent":
        assert "Plan mode is on" in _tool(plugin, action="on", reason="parser")
    else:
        plugin.command("on parser")
    before = _state(plugin)
    child["on"] = True
    assert _tool(plugin, action="off") == pm._CHILD_REFUSAL
    assert _state(plugin) == before
    assert plugin.pre_tool_call("terminal", {"command": "ls"})["action"] == "block"  # the child is still locked
    child["on"] = False
    assert plugin.pre_tool_call("terminal", {"command": "ls"})["action"] == "block"
    assert json.loads(plugin.tool({"action": "inspect"}))["plan_mode"] == "on"
    if entered == "agent":  # the parent's own off still works: no contradictory on/off state
        assert _tool(plugin, action="off") == "Plan mode is off for this session. No approval note will be injected."
        assert plugin.pre_tool_call("terminal", {"command": "ls"}) is None


def test_child_submit_does_not_submit_the_parents_plan(make_plugin, child):
    plugin = make_plugin()
    path = write_plan(plugin)
    before = _state(plugin)
    child["on"] = True
    assert _tool(plugin, action="submit", path=str(path)) == pm._CHILD_REFUSAL
    assert _state(plugin) == before
    child["on"] = False
    assert plugin.pre_tool_call("terminal", {"command": "ls"})["action"] == "block"


def test_child_read_only_actions_still_answer(make_plugin, child):
    plugin = make_plugin()
    plugin.command("on parser")
    before = _state(plugin)
    child["on"] = True
    assert "Plan mode" in _tool(plugin, action="status")
    assert json.loads(plugin.tool({"action": "inspect"}))
    assert json.loads(plugin.tool({"action": "list"}))
    assert _state(plugin) == before
    assert "Unsupported plan_mode action" in _tool(plugin, action="approve")


@pytest.mark.parametrize("platform,in_child,shown", [
    ("telegram", False, True), ("cli", False, True), ("", False, True),
    ("subagent", False, False), ("curator", False, False), ("SUBAGENT", False, False),
    ("cli", True, False), ("telegram", True, False),
])
@pytest.mark.parametrize("profile,hint", [("classic", render.AGENT_HINT), ("lean", render.AGENT_HINT_BRIDGE)])
def test_hint_is_not_injected_into_delegated_children(make_plugin, child, platform, in_child, shown, profile, hint):
    section = make_plugin(plan_profile=profile).ctx.sections["plan-mode.hint"]
    child["on"] = in_child
    assert section["content"]({"platform": platform}) == (hint if shown else "")


def test_hint_tolerates_missing_session_info(make_plugin, child):
    section = make_plugin(plan_profile="classic").ctx.sections["plan-mode.hint"]
    assert section["content"](None) == render.AGENT_HINT
    assert section["content"]({}) == render.AGENT_HINT
