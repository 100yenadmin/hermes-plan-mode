"""0.6.4: a submitted plan still needs an approval route when no card opens."""
import copy
import json
import os

import pytest

from plan_mode import plugin as pm
from test_approval_card import APPROVE, KEEP, answer_card, ask_args, submit
from test_levers import IDS, make_plugin, write_plan
from test_plan_mode import session_env


POINTER = "⏸ Plan rev {revision} is waiting for your approval: type /planmode approve to approve it, or say what to change."
PLANNING = "⏸ Plan mode: nothing changes until you approve the plan."


@pytest.fixture(params=["cli", "terminal", "tui", "desktop", "telegram", ""])
def awaiting(request, make_plugin, session_env):
    platform = request.param
    session_env["HERMES_SESSION_PLATFORM"] = platform
    if platform in {"cli", "terminal"}:
        session_env["HERMES_SESSION_KEY"] = ""
    elif platform in {"tui", "desktop"}:
        session_env["HERMES_UI_SESSION_ID"] = "pointer-tab"
    plugin = make_plugin("approval_card")
    path = write_plan(plugin)
    card = submit(plugin, path)
    key = pm.derive_session_identity().key
    current = plugin._load_state(key)
    assert current["active"] and current["submission"]["status"] == "awaiting"
    assert not current["submission"]["clarify"]["tool_call_id"]
    assert key.startswith("ui:" if platform in {"tui", "desktop"} else
                          "cli:" if platform in {"cli", "terminal"} else "sk:")
    return plugin, path, card, key, platform


def expected(reply, revision=1):
    # A pure append: the streamed reply stays an exact prefix.
    sep = "" if not reply or reply.endswith("\n\n") else "\n" if reply.endswith("\n") else "\n\n"
    return reply + sep + POINTER.format(revision=revision)


def test_pointer_on_every_surface_is_read_only_and_idempotent(awaiting):
    plugin, _, _, _, platform = awaiting
    before = copy.deepcopy(plugin.ctx.state.values)
    asks = plugin._open_asks.copy()
    reply = plugin.transform_llm_output("Saved the plan.  \n", platform=platform)
    assert reply == expected("Saved the plan.  \n") and reply.startswith("Saved the plan.  \n")
    assert PLANNING not in reply
    assert plugin.transform_llm_output(reply, platform=platform) is None
    assert plugin.ctx.state.values == before and plugin._open_asks == asks


def test_pointer_ignores_the_footer_length_limit(awaiting):
    plugin, _, _, _, platform = awaiting
    reply = "x" * 1801
    assert plugin.transform_llm_output(reply, platform=platform) == expected(reply)


@pytest.mark.parametrize("footer", ["off", False])
def test_footer_off_turns_off_the_pointer_too(awaiting, footer):
    # `footer: off` is the documented opt-out that lets another plugin's output transform win.
    plugin, _, _, _, platform = awaiting
    plugin.ctx.settings["footer"] = footer
    assert plugin.transform_llm_output("Saved the plan.", platform=platform) is None


@pytest.mark.parametrize("reply", ["Saved", "Saved  ", "Saved\n", "Saved\n\n", ""])
def test_pointer_is_a_pure_append(awaiting, reply):
    plugin, _, _, _, platform = awaiting
    result = plugin.transform_llm_output(reply, platform=platform)
    assert result.startswith(reply) and result.endswith(POINTER.format(revision=1))
    assert result == expected(reply)


@pytest.mark.parametrize("child", ["subagent", "curator"])
def test_internal_child_turns_get_no_pointer(awaiting, child):
    plugin = awaiting[0]
    assert plugin.transform_llm_output("Child result.", platform=child) is None


@pytest.mark.parametrize("reply", [
    "Type /planmode approve when ready.",
    "Draft\n\n⏸ Plan mode: already shown  \n",
    "Draft\n\nPlan progress 1/2",
    "Draft\n\nExecuting approved plan rev 1",
    "Draft\n\n⏸ Plan rev 1 is waiting",
])
def test_pointer_does_not_duplicate_an_approval_route_or_footer(awaiting, reply):
    plugin, _, _, _, platform = awaiting
    result = plugin.transform_llm_output(reply, platform=platform)
    # An existing approval command keeps the usual chat footer; no pointer is added.
    assert result == (reply + "\n\n" + PLANNING if platform == "telegram" and reply.startswith("Type ") else None)


@pytest.mark.parametrize("ending", ["open-card", "approved", "keep-planning", "off", "no-submission"])
def test_other_states_keep_existing_surface_behavior(awaiting, ending):
    plugin, _, card, key, platform = awaiting
    if ending == "open-card":
        assert plugin.pre_tool_call("clarify", ask_args(card), tool_call_id="live") is None
        current = plugin._load_state(key)["submission"]["clarify"]
        assert (current["nonce"], "live") in plugin._open_asks
    elif ending in {"approved", "keep-planning"}:
        answer_card(plugin, card, APPROVE if ending == "approved" else KEEP)
        assert plugin._load_state(key)["submission"]["status"] == (
            "approved" if ending == "approved" else "rejected")
    elif ending == "off":
        plugin.command("off")
    else:
        plugin.command("on")
        assert "submission" not in plugin._load_state(key)
    result = plugin.transform_llm_output("Draft", platform=platform)
    if platform != "telegram" or ending == "off":
        assert result is None
    elif ending == "approved":
        assert result == "Draft\n\nExecuting approved plan rev 1"
    else:
        assert result == "Draft\n\n" + PLANNING


@pytest.mark.parametrize("marker", ["registry", "persisted-call"])
def test_either_open_card_marker_suppresses_pointer(awaiting, marker):
    plugin, _, card, key, platform = awaiting
    assert plugin.pre_tool_call("clarify", ask_args(card), tool_call_id="live") is None
    current = plugin._load_state(key)
    if marker == "registry":
        current["submission"]["clarify"]["tool_call_id"] = ""
        plugin._save_state(key, current)
    else:
        plugin._open_asks.clear()
    result = plugin.transform_llm_output("Draft", platform=platform)
    assert result == ("Draft\n\n" + PLANNING if platform == "telegram" else None)


def test_submission_without_clarify_gets_pointer(make_plugin, session_env):
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    plugin = make_plugin(plan_profile="classic")
    path = write_plan(plugin)
    gate = plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
    assert gate["action"] == "approve"
    plugin._ledger.record_response(pattern_key="plugin_rule:" + gate["rule_key"],
                                   cancelled="turn ended", tool_call_id="submit")
    json.loads(plugin.tool({"action": "submit"}))
    current = plugin._load_state("sk:unit-session")
    assert current["submission"]["status"] == "awaiting" and "clarify" not in current["submission"]
    assert plugin.transform_llm_output("Saved", platform="cli") == expected("Saved")


def test_pointer_uses_latest_revision_and_ignores_other_submissions_asks(awaiting):
    plugin, path, card, _, platform = awaiting
    answer_card(plugin, card, KEEP)
    submit(plugin, path)
    plugin._open_asks.add(("unrelated-nonce", "other-call"))
    assert plugin.transform_llm_output("Revised", platform=platform) == expected("Revised", 2)


@pytest.mark.parametrize("fallback", ["session", "cli"])
def test_ui_fallback_is_read_only(make_plugin, session_env, fallback):
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    if fallback == "cli":
        session_env["HERMES_SESSION_KEY"] = ""
    plugin = make_plugin("approval_card")
    submit(plugin, write_plan(plugin))
    key = pm.derive_session_identity().key
    current = plugin._load_state(key)
    current["owner_pid"] = os.getpid() + 1  # reading output must not claim another process's state
    plugin._save_state(key, current)
    session_env.update(HERMES_UI_SESSION_ID="new-tab", HERMES_SESSION_PLATFORM="desktop")
    before = copy.deepcopy(plugin.ctx.state.values)
    assert plugin.transform_llm_output("Saved", platform="desktop") == expected("Saved")
    assert plugin.ctx.state.values == before


def test_pointer_fails_open_on_state_error(awaiting, monkeypatch):
    plugin, _, _, _, platform = awaiting
    def fail(_key):
        raise OSError("state unavailable")
    monkeypatch.setattr(plugin, "_load_state", fail)
    assert plugin.transform_llm_output("Draft", platform=platform) is None
