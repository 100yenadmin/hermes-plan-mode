"""1.3.0: the reply names the calls plan mode blocked, because Hermes shows blocked calls nowhere (#151276)."""
import json

import pytest

from plan_mode import plugin as pm
from plan_mode import render
from test_levers import make_plugin, write_plan
from test_plan_mode import session_env

RECEIPT = "🔒 Plan mode blocked"


def blocked(plugin, name, args, platform="", turn="t1"):
    """Run one call through the lock the way Hermes does: pre_tool_call blocks, post_tool_call reports it.

    Like Hermes, post_tool_call gets ids and the outcome but no platform (``platform`` is unused there)."""
    decision = plugin.pre_tool_call(name, args, tool_call_id=f"call-{name}")
    assert decision and decision["action"] == "block", decision
    message = decision["message"]
    plugin.post_tool_call(name, args, tool_call_id=f"call-{name}", turn_id=turn, session_id="s", task_id="",
                          status="blocked", error_type="plugin_block", error_message=message,
                          result=json.dumps({"error": message}))
    return message


def appended(reply, line):
    sep = "" if not reply or reply.endswith("\n\n") else "\n" if reply.endswith("\n") else "\n\n"
    return reply + sep + line


@pytest.fixture
def planning(make_plugin, session_env):
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    session_env["HERMES_SESSION_KEY"] = ""
    plugin = make_plugin("")
    plugin.command("on parser")
    return plugin


def test_cli_reply_names_the_blocked_call_once(planning):
    blocked(planning, "terminal", {"command": "mkdir -p   /repo/.hermes/plans"}, platform="cli")
    reply = planning.transform_llm_output("Here is the plan.", platform="cli")
    assert reply == appended("Here is the plan.", "🔒 Plan mode blocked 1 tool call: terminal (mkdir -p /repo/.hermes/plans).")
    assert planning.transform_llm_output("Next reply.", platform="cli") is None  # named once, then forgotten


def test_counts_several_calls_and_lists_at_most_three(planning):
    for command in ("pytest -q", "git status", "make", "ls"):
        blocked(planning, "terminal", {"command": command}, platform="cli")
    blocked(planning, "execute_code", {"code": "print(1)\nprint(2)"}, platform="cli")
    reply = planning.transform_llm_output("Plan ready.\n", platform="cli")
    assert reply.startswith("Plan ready.\n")
    assert reply.endswith("🔒 Plan mode blocked 5 tool calls: terminal (pytest -q), terminal (git status), "
                          "terminal (make) and 2 more.")


def test_long_details_are_cut_to_one_short_line(planning):
    blocked(planning, "write_file", {"path": "/repo/" + "a" * 100 + ".py", "content": "x"}, platform="cli")
    line = planning.transform_llm_output("ok", platform="cli").split("\n")[-1]
    detail = line.split("write_file (")[1].rsplit(").", 1)[0]
    assert len(detail) == 60 and detail.endswith("…")


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
def test_chat_replies_name_tools_only(make_plugin, session_env, platform):
    session_env["HERMES_SESSION_PLATFORM"] = platform
    session_env["HERMES_SESSION_KEY"] = f"agent:main:{platform}:dm:42"
    plugin = make_plugin("")
    plugin.command("on parser")
    blocked(plugin, "terminal", {"command": "curl -H 'token: secret' example.invalid"}, platform=platform)
    reply = plugin.transform_llm_output("Here is the plan.", platform=platform)
    assert "🔒 Plan mode blocked 1 tool call: terminal." in reply
    assert "secret" not in reply and "curl" not in reply
    assert reply.startswith("Here is the plan.")


@pytest.mark.parametrize("platform", ["api_server", "webhook"])
def test_program_facing_replies_stay_unchanged(make_plugin, session_env, platform):
    session_env["HERMES_SESSION_PLATFORM"] = platform
    session_env["HERMES_SESSION_KEY"] = f"agent:main:{platform}:dm:7"
    plugin = make_plugin("")
    plugin.command("on parser")
    blocked(plugin, "terminal", {"command": "ls"}, platform=platform)
    reply = plugin.transform_llm_output("Here is the plan.", platform=platform)
    assert reply is None or RECEIPT not in reply


def test_footer_off_turns_the_receipt_off(planning):
    planning.ctx.settings["footer"] = "off"
    blocked(planning, "terminal", {"command": "ls"}, platform="cli")
    assert planning.transform_llm_output("Here is the plan.", platform="cli") is None


def test_other_blocks_and_successes_are_not_named(planning):
    # Another plugin's block, a guardrail block, a plain error and a successful call are not the lock's doing.
    planning.post_tool_call("terminal", {"command": "ls"}, status="blocked", error_message="Blocked by policy X",
                            result=json.dumps({"error": "Blocked by policy X"}))
    planning.post_tool_call("terminal", {"command": "ls"}, status="error", error_message="Plan mode is on: fake",
                            platform="cli")
    planning.post_tool_call("read_file", {"path": "/x"}, status="ok", result="{}")
    assert planning.transform_llm_output("Here is the plan.", platform="cli") is None


def test_message_from_the_result_when_error_message_is_missing(planning):
    message = render.BLOCKED_TOOL_MESSAGE.format(name="patch")
    planning.post_tool_call("patch", {"path": "/repo/a.py"}, status="blocked", result=json.dumps({"error": message}))
    assert planning.transform_llm_output("x", platform="cli").endswith("blocked 1 tool call: patch (/repo/a.py).")


def test_malformed_reports_never_raise(planning):
    for kwargs in ({"status": "blocked"}, {"status": "blocked", "result": "not json"},
                   {"status": "blocked", "error_message": 7, "result": "[1]"}):
        assert planning.post_tool_call("terminal", None, **kwargs) is None
    assert planning.transform_llm_output("x", platform="cli") is None


def test_blocks_of_an_earlier_turn_are_never_named_late(planning):
    # Turn 1 blocked a call but ended with no text (Hermes then skips transform_llm_output).
    blocked(planning, "terminal", {"command": "make"}, turn="t1")
    blocked(planning, "terminal", {"command": "ls"}, turn="t2")
    assert planning.transform_llm_output("Turn 2.", platform="cli", turn_id="t2").endswith(
        "blocked 1 tool call: terminal (ls).")
    blocked(planning, "terminal", {"command": "pwd"}, turn="t3")
    assert planning.transform_llm_output("Turn 4.", platform="cli", turn_id="t4") is None
    assert planning._turn_blocks == {}


def test_footer_off_and_program_platforms_still_forget_the_blocks(planning):
    planning.ctx.settings["footer"] = "off"
    blocked(planning, "terminal", {"command": "ls"})
    assert planning.transform_llm_output("x", platform="cli") is None
    planning.ctx.settings["footer"] = "auto"
    assert planning.transform_llm_output("y", platform="cli") is None
    blocked(planning, "terminal", {"command": "ls"})
    assert planning.transform_llm_output("z", platform="api_server") is None
    assert planning._turn_blocks == {}


@pytest.mark.parametrize("platform", ["", "cli"])
def test_gateway_session_never_shows_details(make_plugin, session_env, platform):
    # A gateway agent with an empty or local-looking platform is still a chat: its key is a session key.
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"
    session_env["HERMES_SESSION_KEY"] = "agent:main:telegram:dm:42"
    plugin = make_plugin("")
    plugin.command("on parser")
    blocked(plugin, "terminal", {"command": "cat secret.txt"})
    reply = plugin.transform_llm_output("Plan.", platform=platform)
    assert reply == "Plan.\n\n🔒 Plan mode blocked 1 tool call: terminal."


def test_long_chat_reply_gets_no_receipt(make_plugin, session_env):
    session_env["HERMES_SESSION_PLATFORM"] = "discord"
    session_env["HERMES_SESSION_KEY"] = "agent:main:discord:dm:9"
    plugin = make_plugin("")
    plugin.command("on parser")
    blocked(plugin, "terminal", {"command": "ls"})
    assert plugin.transform_llm_output("x" * (pm._FOOTER_MAX_CHARS + 1), platform="discord") is None
    assert plugin._turn_blocks == {}


def test_chat_footer_follows_the_receipt_and_is_not_doubled(make_plugin, session_env):
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"
    session_env["HERMES_SESSION_KEY"] = "agent:main:telegram:dm:42"
    plugin = make_plugin("")
    plugin.command("on parser")
    footer = "⏸ Plan mode: nothing changes until you approve the plan."
    blocked(plugin, "terminal", {"command": "ls"})
    assert plugin.transform_llm_output("Plan.", platform="telegram") == (
        "Plan.\n\n🔒 Plan mode blocked 1 tool call: terminal.\n\n" + footer)
    blocked(plugin, "terminal", {"command": "ls"})
    reply = plugin.transform_llm_output("Plan.\n\n" + footer, platform="telegram")
    assert reply == "Plan.\n\n" + footer + "\n\n🔒 Plan mode blocked 1 tool call: terminal."
    assert reply.count(footer) == 1


def test_receipt_goes_before_the_approval_pointer(make_plugin, session_env):
    from test_approval_card import submit

    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    session_env["HERMES_SESSION_KEY"] = ""
    plugin = make_plugin("approval_card")
    path = write_plan(plugin)
    blocked(plugin, "terminal", {"command": "pytest"}, platform="cli")
    submit(plugin, path)
    reply = plugin.transform_llm_output("Saved the plan.", platform="cli")
    lines = reply.split("\n")
    assert reply.startswith("Saved the plan.")
    assert lines[-3] == "🔒 Plan mode blocked 1 tool call: terminal (pytest)."
    assert lines[-1].startswith("⏸ Plan rev 1 is waiting for your approval")


def test_no_commit_block_during_execution_is_named(make_plugin, session_env, monkeypatch):
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    session_env["HERMES_SESSION_KEY"] = ""
    plugin = make_plugin("")
    key = pm.derive_session_identity().key
    state = plugin._load_state(key)
    state.update(active=False, phase="executing", approved_revision=1, approved_path="/p.md")
    plugin._save_state(key, state)
    message = render.NO_COMMIT_BLOCK
    plugin.post_tool_call("terminal", {"command": "git commit -m x"}, status="blocked", error_message=message,
                          result=json.dumps({"error": message}))
    assert plugin.transform_llm_output("Done.", platform="cli").endswith(
        "🔒 Plan mode blocked 1 tool call: terminal (git commit -m x).")


def test_tracked_sessions_are_bounded(planning):
    for n in range(pm._RECEIPT_SESSIONS):
        planning._turn_blocks[f"sk:other-{n}"] = {"count": 1, "calls": []}
    blocked(planning, "terminal", {"command": "ls"}, platform="cli")
    assert len(planning._turn_blocks) == pm._RECEIPT_SESSIONS and "sk:other-0" not in planning._turn_blocks
    assert planning.transform_llm_output("x", platform="cli").endswith("terminal (ls).")
