"""Experimental levers: default-off goldens captured from 7e77318, then opt-in paths."""
from datetime import datetime
import hashlib
import itertools
import json
from pathlib import Path

import pytest

from plan_mode import plugin as pm
from plan_mode import render
from plan_mode.approval import approval_text
from test_plan_mode import FakeContext, session_env


LEVER_NAMES = {
    "plan_once", "lean_craft", "todo_batch", "codex_compact", "question_shape",
    "sticky", "reentry", "approved_text", "light_notes",
}
TEXT = "# Parser plan\n\nChange parser.py and its caller cli.py. Add a behaviour test; run pytest tests/test_parser.py."
IDS = dict(session_id="s", turn_id="t", api_request_id="r", tool_call_id="submit")


@pytest.fixture
def make_plugin(session_env, tmp_path, monkeypatch):
    monkeypatch.delenv("PLAN_MODE_LEVERS", raising=False)
    home = tmp_path / "test-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    session_env["TERMINAL_CWD"] = str(tmp_path)
    def make(levers="", **settings):
        ctx = FakeContext()
        ctx.settings.update(levers=levers, **settings)
        instance = pm.PlanModePlugin(ctx)
        instance.register()
        return instance
    return make


def write_plan(plugin, text=TEXT):
    plugin.command("on parser")
    plans = Path(plugin.pre_llm_call()["context"].split("files may be written only under ")[1].split(".\n")[0])
    path = plans / "parser.md"
    assert plugin.pre_tool_call("write_file", {"path": str(path)}) is None
    path.write_text(text, encoding="utf-8")
    return path


def approve_gate(plugin, path):
    gate = plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
    plugin.ctx.hooks["post_approval_response"](
        pattern_key="plugin_rule:" + gate["rule_key"], choice="once", tool_call_id="submit",
    )
    return json.loads(plugin.tool({"action": "submit"}))


def test_default_off_golden(make_plugin, monkeypatch):
    golden = json.loads((Path(__file__).parent / "fixtures/default_off_035.json").read_text())
    assert golden["approved"]["approved"] is True
    assert "clarify" in golden["bypass"]
    plugin = make_plugin()
    for style, commits, skill in itertools.product(["core", "compact"], [False, True], ["", "project-plan"]):
        assert render.planning_note("/workspace/.hermes/plans", datetime(2026, 10, 11, 12, 34, 56),
                                    style=style, commits=commits, plan_skill=skill) == golden["notes"][f"{style}:{commits}:{skill}"]
    for commits in [False, True]:
        assert render.executing_pointer({"approved_path": "/plans/fixed.md", "approved_revision": 3}, commits=commits) == golden["pointers"][str(commits)]
    for name, expected in golden["constants"].items():
        assert getattr(pm, name) == expected
    for platform, expected in golden["approvals"].items():
        assert approval_text("# Fixed plan\n\n## Steps\n1. Change parser.py\n2. Run tests", "/plans/fixed.md", 3, platform) == expected
    plugin._save_command_state = lambda *args: None
    monkeypatch.setattr(pm, "plan_digest", lambda path: ("digest", "# Fixed plan"))
    monkeypatch.setattr(pm.secrets, "token_hex", lambda size: "fixednonce")
    def state():
        return {"active": True, "activation_id": "fixed", "submission": {
            "path": "/plans/fixed.md", "revision": 3, "rule_key": "plan-mode:fixed", "digest": "digest",
            "tool_call_id": "submit", "status": "pending"}}
    plugin._ledger.mark_inflight("plan-mode:fixed", "submit")
    plugin._ledger.record_response(pattern_key="plugin_rule:plan-mode:fixed", choice="once", tool_call_id="submit")
    assert plugin._complete_submission("raw", "storage", state()) == golden["approved"]
    assert plugin._complete_submission("raw", "storage", state()) == golden["bypass"]
    assert "post_api_request" not in plugin.ctx.hooks
    assert "post_llm_call" not in plugin.ctx.hooks


@pytest.mark.parametrize("value,expected", [("all", LEVER_NAMES), ("sticky, reentry unknown", {"sticky", "reentry"}), ("", set())])
def test_switch(make_plugin, value, expected, monkeypatch):
    plugin = make_plugin(value)
    assert plugin._levers() == expected
    plugin.ctx.settings = {"plan_mode.levers": "sticky"}
    assert plugin._levers() == {"sticky"}
    plugin.ctx.settings = {"levers": ""}
    monkeypatch.setenv("PLAN_MODE_LEVERS", "reentry")
    assert plugin._levers() == {"reentry"}
    plugin.ctx.settings = {"levers": "sticky"}
    assert plugin._levers() == {"sticky"}


@pytest.mark.parametrize("platform", ["cli", "tui", "desktop", "telegram", "slack", "discord", "mattermost"])
def test_plan_once_fallback_surfaces(make_plugin, session_env, platform):
    session_env["HERMES_SESSION_PLATFORM"] = platform
    plugin = make_plugin("plan_once")
    plugin.command("on")
    note = plugin.pre_llm_call()["context"]
    assert "post_api_request" in plugin.ctx.hooks
    assert "Write the complete plan as your reply text" not in note
    if platform == "mattermost":
        assert "3–5 line summary plus the path" in note
        assert "3,000 characters" in note
        assert "longer" in note and "complete plan" in note
    else:
        assert "Show the complete plan in your reply" in note


def test_plan_once_fallback_file_digest(make_plugin, session_env):
    session_env["HERMES_SESSION_PLATFORM"] = "mattermost"
    plugin = make_plugin("plan_once")
    path = write_plan(plugin)
    gate = plugin.pre_tool_call("plan_mode", {"action": "submit"}, **IDS)
    assert gate["action"] == "approve" and TEXT in gate["message"]
    state = plugin._load_state("sk:unit-session")
    assert state["submission"]["digest"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert Path(state["submission"]["path"]) == path


def test_plan_once_fallback_symlink_refusal(make_plugin, tmp_path):
    plugin = make_plugin("plan_once")
    plugin.command("on")
    plans = tmp_path / ".hermes/plans"
    plans.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    plans.symlink_to(outside, target_is_directory=True)
    result = plugin.pre_tool_call("write_file", {"path": str(plans / "plan.md"), "content": TEXT})
    assert result["action"] == "block"
    assert not list(outside.iterdir())


def test_lean_craft(make_plugin, monkeypatch):
    plugin = make_plugin("lean_craft")
    plugin.command("on")
    note = plugin.pre_llm_call()["context"]
    for text in ["2–5-minute", "copy-pasteable", "deliberately failing", "TDD skill", "absolute path", "after implementing"]:
        assert text in note
    path = write_plan(plugin)
    assert "no red-first ceremony" in approve_gate(plugin, path)["message"]
    assert "no red-first ceremony" in plugin.pre_llm_call()["context"]
    plugin.ctx.settings["plan_style"] = "core"
    plugin.command("on")
    assert "no red-first ceremony" not in plugin._execute_hint()
    assert "deliberately failing" not in plugin.pre_llm_call()["context"]
    plugin.ctx.settings.update(plan_style="compact", plan_skill="custom")
    monkeypatch.setattr(pm, "_skill_view_block_reason", lambda: None)
    assert "deliberately failing" not in plugin.pre_llm_call()["context"]


def test_todo_batch_clear_at_turn_end(make_plugin):
    plugin = make_plugin("todo_batch")
    path = write_plan(plugin)
    result = approve_gate(plugin, path)
    for text in ["4 or more steps", "same response", "never in a response by itself", "batch independent read-only"]:
        assert text in result["message"]
    plugin.post_llm_call(**IDS)
    assert plugin.pre_llm_call() is None


def test_todo_batch_keeps_current_execution_items(make_plugin):
    plugin = make_plugin("todo_batch")
    path = write_plan(plugin)
    approve_gate(plugin, path)
    todos = [{"id": "1", "content": "work", "status": "in_progress"}]
    plugin.post_tool_call("todo_list", {"todos": todos}, status="ok", result=json.dumps({"todos": todos}))
    plugin.post_llm_call(**IDS)
    assert "Executing the approved plan" in plugin.pre_llm_call()["context"]
    assert "done" in plugin.command("done")


def test_codex_compact(make_plugin):
    plugin = make_plugin("codex_compact")
    plugin.command("on")
    note = plugin.pre_llm_call()["context"]
    for text in ["Summary", "Key changes", "Test plan", "Assumptions", "every touched file", "every consumer", "about 5 bullets", "time estimates", "RED/GREEN", "replaces any other plan template"]:
        assert text in note
    assert "Steps (numbered" not in note


def test_question_shape(make_plugin):
    plugin = make_plugin("question_shape")
    plugin.command("on")
    note = plugin.pre_llm_call()["context"]
    for text in ["ask with the clarify tool", "reading the repo cannot settle", "prefer 1 question", "at most 3",
                 "2–3 short choices", "label — consequence", "recommended first", "Do not guess these"]:
        assert text in note
    assert "up to 4 short choices" not in note
    assert "Otherwise record an assumption" not in note  # v1 wording suppressed asking (round 3, N4)


def test_sticky(make_plugin):
    plugin = make_plugin("sticky")
    plugin.command("on")
    assert '"just do it"' in plugin.pre_llm_call()["context"]
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"


@pytest.mark.parametrize("approved", [False, True])
def test_reentry(make_plugin, approved):
    plugin = make_plugin("reentry")
    path = write_plan(plugin)
    if approved:
        approve_gate(plugin, path)
    else:
        plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
    plugin.command("on refine")
    note = plugin.pre_llm_call()["context"]
    assert str(path) in note
    assert "same task" in note and "different task" in note
    path.unlink()
    plugin.command("on again")
    assert str(path) not in plugin.pre_llm_call()["context"]


@pytest.mark.parametrize("route", ["gate", "typed", "clarify"])
def test_approved_text(make_plugin, route):
    plugin = make_plugin("approved_text")
    path = write_plan(plugin, TEXT + "\n" + "x" * 6000)
    if route == "gate":
        message = approve_gate(plugin, path)["message"]
    elif route == "typed":
        plugin.command("approve")
        message = plugin.pre_llm_call()["context"].split("\n\nExecuting")[0]
    else:
        plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
        result = json.loads(plugin.tool({"action": "submit"}))
        clarify = result["clarify"]
        args = {"questions": [clarify]}
        assert plugin.pre_tool_call("clarify", args, tool_call_id="clarify") is None
        payload = {"question": clarify["question"], "user_response": clarify["choices"][0]}
        plugin.post_tool_call("clarify", args, tool_call_id="clarify", status="ok", result=json.dumps(payload))
        transformed = plugin.transform_tool_result("clarify", args, result=json.dumps(payload), tool_call_id="clarify")
        message = json.loads(transformed)["plan_mode_approval"]
    assert TEXT in message
    excerpt = message[message.index("Plan: "):]
    assert len(excerpt) <= 3500
    assert f"full plan at {path}" in excerpt


def test_light_notes(make_plugin):
    plugin = make_plugin("light_notes")
    plugin.command("on")
    assert "1. Explore" in plugin.pre_llm_call()["context"]
    note = plugin.pre_llm_call()["context"]
    assert len(note) <= 400 and "plan_mode" in note
    assert "1. Explore" not in note
    plugin.command("reject revise")
    assert "1. Explore" in plugin.pre_llm_call()["context"]
    assert len(plugin.pre_llm_call()["context"]) <= 400
