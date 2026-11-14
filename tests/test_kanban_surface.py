"""#96: keyless Kanban workers follow their task card without changing plan mode."""
from copy import deepcopy
import json

import pytest

from plan_mode import plugin as pm
from plan_mode import render
from test_levers import IDS, make_plugin
from test_plan_mode import session_env


@pytest.fixture(params=["HERMES_SESSION_SOURCE", "HERMES_SESSION_PLATFORM"])
def kanban(session_env, request):
    session_env.clear()
    session_env[request.param] = "kanban"
    return session_env


@pytest.fixture(params=["classic", "lean"])
def profile(request):
    return request.param


def _tool(plugin, action):
    return json.loads(plugin.tool({"action": action}))["message"]


def _bookkeeping(plugin):
    return deepcopy((plugin.ctx.state.values, plugin._ledger._entries,
                     plugin._open_asks, plugin._card_baselines, plugin._active_keys))


@pytest.mark.parametrize("action", ["on", "off", "submit"])
def test_kanban_tool_refuses_without_changes(make_plugin, kanban, profile, action):
    plugin = make_plugin(plan_profile=profile)
    before = _bookkeeping(plugin)
    message = _tool(plugin, action)
    assert message == pm._KANBAN_REFUSAL
    assert "Upgrade Hermes" not in message
    assert _bookkeeping(plugin) == before


def test_kanban_status_names_the_surface(make_plugin, kanban, profile):
    plugin = make_plugin(plan_profile=profile)
    before = _bookkeeping(plugin)
    assert _tool(plugin, "status").startswith(
        "Plan mode is unavailable on this surface: this is a Kanban worker")
    assert _bookkeeping(plugin) == before


def test_kanban_slash_command_refuses(make_plugin, kanban, profile):
    plugin = make_plugin(plan_profile=profile)
    before = _bookkeeping(plugin)
    assert plugin.ctx.commands["planmode"]("on") == pm._KANBAN_REFUSAL
    assert _bookkeeping(plugin) == before


def test_kanban_submit_hook_blocks_before_bookkeeping(make_plugin, kanban, profile):
    plugin = make_plugin(plan_profile=profile)
    # Preserve existing approval bookkeeping as well as empty plugin state.
    plugin._ledger.mark_inflight("existing-rule", "existing-call")
    plugin._open_asks.add(("existing-nonce", "existing-call"))
    plugin._card_baselines["existing-session"] = {"text": "existing card"}
    before = _bookkeeping(plugin)
    assert plugin.pre_tool_call("plan_mode", {"action": "submit"}, **IDS) == {
        "action": "block", "message": pm._KANBAN_REFUSAL}
    assert _bookkeeping(plugin) == before


@pytest.mark.parametrize("action", ["on", "off"])
def test_kanban_on_off_hook_still_allows_dispatch(make_plugin, kanban, profile, action):
    plugin = make_plugin(plan_profile=profile)
    before = _bookkeeping(plugin)
    assert plugin.pre_tool_call("plan_mode", {"action": action}, **IDS) is None
    assert _bookkeeping(plugin) == before


def test_kanban_hint_uses_session_surface_not_info_platform(make_plugin, kanban, profile):
    section = make_plugin(plan_profile=profile).ctx.sections["plan-mode.hint"]
    assert section["content"]({"platform": "cli"}) == render.KANBAN_HINT
    assert len(render.KANBAN_HINT) <= 200


@pytest.mark.parametrize("platform,child", [("cli", True), ("subagent", False), ("curator", False)])
def test_kanban_hint_still_suppresses_helpers(make_plugin, kanban, profile, monkeypatch, platform, child):
    monkeypatch.setattr(pm, "_delegated_child", lambda: child)
    section = make_plugin(plan_profile=profile).ctx.sections["plan-mode.hint"]
    assert section["content"]({"platform": platform}) == ""


def test_keyed_kanban_keeps_normal_hint_and_can_activate(make_plugin, kanban, profile, tmp_path):
    kanban.update(HERMES_SESSION_KEY="kanban-session", TERMINAL_CWD=str(tmp_path))
    plugin = make_plugin(plan_profile=profile)
    expected = render.AGENT_HINT if profile == "classic" else render.AGENT_HINT_BRIDGE
    assert plugin.ctx.sections["plan-mode.hint"]["content"]({"platform": "cli"}) == expected
    assert "Plan mode is on" in _tool(plugin, "on")
    assert plugin._load_state("sk:kanban-session")["active"] is True


@pytest.mark.parametrize("source", ["telegram", "api_server", "cron"])
def test_other_keyless_surfaces_keep_hint_and_refusal(make_plugin, session_env, profile, source):
    session_env.clear()
    session_env["HERMES_SESSION_SOURCE"] = source
    plugin = make_plugin(plan_profile=profile)
    expected = render.AGENT_HINT if profile == "classic" else render.AGENT_HINT_BRIDGE
    assert plugin.ctx.sections["plan-mode.hint"]["content"]({"platform": "cli"}) == expected
    assert "session binding" in _tool(plugin, "on")


def test_hint_falls_back_when_session_reader_raises(make_plugin, profile, monkeypatch):
    section = make_plugin(plan_profile=profile).ctx.sections["plan-mode.hint"]

    def broken_reader(key, default=""):
        raise RuntimeError("session context unavailable")

    monkeypatch.setattr(pm, "_session_reader", lambda: broken_reader)
    expected = render.AGENT_HINT if profile == "classic" else render.AGENT_HINT_BRIDGE
    assert section["content"]({"platform": "cli"}) == expected


@pytest.mark.parametrize("identity,source,expected", [
    (pm.SessionIdentity(None, surface="KANBAN"), "", True),
    (pm.SessionIdentity(None, non_cli_without_key=True, surface="cli"), " KanBan ", True),
    (pm.SessionIdentity(None, unsupported=True, surface="kanban"), "kanban", False),
    (pm.SessionIdentity("sk:bound", surface="kanban"), "kanban", False),
    (pm.SessionIdentity(None, surface="telegram"), "telegram", False),
])
def test_kanban_worker_predicate(session_env, identity, source, expected):
    session_env.clear()
    session_env["HERMES_SESSION_SOURCE"] = source
    assert pm._kanban_worker(identity) is expected
