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

pytestmark = pytest.mark.usefixtures("directive_lean")  # the directive path; see conftest.directive_lean


LEVER_NAMES = {
    "plan_once", "write_once", "lean_craft", "todo_batch", "codex_compact", "question_shape",
    "sticky", "reentry", "approved_text", "light_notes", "plan_budget",
    "commit_override", "approval_ask", "bridge_calls", "blocked_tools", "skill_policy",
    "test_commands", "choice_fidelity", "refactor_scope", "todo_inline",
    "rename_scope", "test_plan_only", "choice_record", "edit_batch", "brief_final", "exec_skill_guard", "approval_card",
}
NEW_044 = {"rename_scope", "test_plan_only", "choice_record", "edit_batch", "brief_final"}
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
    plugin = make_plugin(plan_profile="classic")
    assert plugin._levers() == set()
    for style, commits, skill in itertools.product(["core", "compact"], [False, True], ["", "project-plan"]):
        assert render.planning_note("/workspace/.hermes/plans", datetime(2026, 10, 11, 12, 34, 56),
                                    style=style, commits=commits, plan_skill=skill,
                                    levers=plugin._note_levers()) == golden["notes"][f"{style}:{commits}:{skill}"]
    for commits in [False, True]:
        assert render.executing_pointer({"approved_path": "/plans/fixed.md", "approved_revision": 3},
                                        commits=commits, levers=plugin._note_levers()) == golden["pointers"][str(commits)]
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
    # The lever hooks are registered for every profile but do nothing without their levers.
    assert plugin.post_api_request(assistant_message={"content": "x" * 200, "tool_calls": []}) is None
    assert plugin._reply_captures == {}


@pytest.mark.parametrize("value,expected", [("all", LEVER_NAMES), ("sticky, reentry unknown", {"sticky", "reentry"}), ("", set())])
def test_switch(make_plugin, value, expected, monkeypatch):
    plugin = make_plugin(value, plan_profile="classic")
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
    assert "Write the complete plan in your reply, in Markdown, between a line <proposed_plan>" not in note
    if platform == "mattermost":
        assert "3–5 line summary plus the path" in note
        assert "3,000 characters" in note
        assert "longer" in note and "complete plan" in note
    else:
        assert "Show the complete plan in your reply" in note


@pytest.mark.parametrize("profile", ["classic", "lean"])
def test_plan_once_fallback_file_digest(make_plugin, session_env, profile):
    session_env["HERMES_SESSION_PLATFORM"] = "mattermost"
    plugin = make_plugin("plan_once", plan_profile=profile)
    path = write_plan(plugin)
    gate = plugin.pre_tool_call("plan_mode", {"action": "submit"}, **IDS)
    assert gate["action"] == "approve" and TEXT in gate["message"]
    state = plugin._load_state("sk:unit-session")
    assert state["submission"]["digest"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert Path(state["submission"]["path"]) == path


@pytest.mark.parametrize("profile", ["classic", "lean"])
def test_plan_once_fallback_symlink_refusal(make_plugin, tmp_path, profile):
    plugin = make_plugin("plan_once", plan_profile=profile)
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


def test_transform_without_note_touches_no_state(make_plugin, monkeypatch):
    plugin = make_plugin("", plan_profile="classic")
    plugin.command("on")
    monkeypatch.setattr(plugin, "_state_for_hook", lambda *a, **k: pytest.fail("state accessed"))
    result = json.dumps({"question": "q", "user_response": "a"})
    assert plugin.ctx.hooks["transform_tool_result"]("clarify", {}, result=result, tool_call_id="c") is None


def test_profile_switch_needs_no_restart(make_plugin):
    plugin = make_plugin("", plan_profile="classic")
    approve_gate(plugin, write_plan(plugin))
    turn_end = plugin.ctx.hooks["post_llm_call"]  # registered at startup, under classic
    turn_end(**IDS)
    assert "Executing the approved plan" in plugin.pre_llm_call()["context"]  # classic keeps the pointer
    plugin.ctx.settings["plan_profile"] = "lean"
    turn_end(**IDS)
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


def test_plan_budget(make_plugin):
    budget = ["under about 1,200 characters", "one bullet per file or behaviour", "who consumes it",
              "not how to write it", "no test-case lists", "commands only", "leave the section out"]
    for levers in ("plan_budget", "plan_budget codex_compact"):
        plugin = make_plugin(levers, plan_style="compact")
        plugin.command("on")
        note = plugin.pre_llm_call()["context"]
        assert all(text in note for text in budget), levers
        if "codex_compact" in levers:
            assert note.index("about 5 bullets") < note.index("under about 1,200 characters")
    # Core style keeps Hermes' own craft, so the budget does not apply; without the lever nothing changes.
    for levers, style in (("plan_budget", "core"), ("codex_compact", "compact"), ("", "compact")):
        plugin = make_plugin(levers, plan_style=style)
        plugin.command("on")
        assert "under about 1,200 characters" not in plugin.pre_llm_call()["context"], (levers, style)


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


@pytest.mark.parametrize("route", ["gate", "typed", "clarify", "clarify_list"])
@pytest.mark.parametrize("profile", ["classic", "lean"])
def test_approved_text(make_plugin, route, profile):
    plugin = make_plugin("approved_text", plan_profile=profile)
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
        if route == "clarify_list":
            payload = [payload]  # legacy hosts return a bare list of answers
        plugin.post_tool_call("clarify", args, tool_call_id="clarify", status="ok", result=json.dumps(payload))
        transformed = plugin.transform_tool_result("clarify", args, result=json.dumps(payload), tool_call_id="clarify")
        decoded = json.loads(transformed)
        assert decoded.get("result", payload) == payload
        message = decoded["plan_mode_approval"]
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


def test_commit_override_lever(make_plugin, tmp_path):
    rule = f"{render.NO_COMMITS} {render.COMMIT_OVERRIDE}"
    pointer_state = {"approved_path": "/plans/fixed.md", "approved_revision": 1}
    assert render.no_commits() == render.NO_COMMITS and render.no_commits({"commit_override"}) == rule
    plugin = make_plugin("commit_override")
    path = write_plan(plugin)
    assert rule in plugin.pre_llm_call()["context"]
    result = approve_gate(plugin, path)
    assert result["approved"] and rule in result["message"]
    assert rule in render.executing_pointer(pointer_state, commits=False, levers={"commit_override"})
    # Without the lever (classic, and the 0.4.0 lean set) the 0.3.5 wording stays byte-identical.
    for levers in (set(), set(pm.LEAN_LEVERS)):
        assert render.COMMIT_OVERRIDE not in render.executing_pointer(pointer_state, commits=False, levers=levers)
    assert render.COMMIT_OVERRIDE not in render.executing_pointer(pointer_state, commits=True, levers={"commit_override"})
    allowed = make_plugin("commit_override", allow_commits=True)
    assert allowed._commit_rule() == ""


@pytest.mark.parametrize("levers,asks", [("approval_ask", True), ("", False), ("lean_craft,sticky", False)])
def test_approval_ask_lever(make_plugin, monkeypatch, levers, asks):
    plugin = make_plugin(levers, plan_profile="classic")
    plugin._save_command_state = lambda *args: None
    monkeypatch.setattr(pm, "plan_digest", lambda path: ("digest", "# Fixed plan"))
    state = {"active": True, "activation_id": "fixed", "submission": {
        "path": "/plans/fixed.md", "revision": 3, "rule_key": "plan-mode:fixed", "digest": "digest",
        "tool_call_id": "submit", "status": "pending"}}
    result = plugin._complete_submission("raw", "storage", state)
    assert result["clarify"]["choices"] == ["Approve plan rev 3", "Keep planning"]
    assert (pm._APPROVAL_ASK in result["message"]) is asks
    if asks:
        assert "not multi-select). " + pm._APPROVAL_ASK + "If the user picks" in result["message"]
    else:
        assert "not multi-select). If the user picks" in result["message"]


@pytest.mark.parametrize("lever,text", [
    ("bridge_calls", render.BRIDGE_SUBMIT),
    ("blocked_tools", render.BLOCKED_TOOLS),
    ("skill_policy", render.SKILL_POLICY),
])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("variant", ["file", "reply_plan", "mattermost"])
def test_042_planning_levers(lever, text, enabled, variant):
    levers = {lever} if enabled else set()
    if variant != "file":
        levers.add("plan_once")
    note = render.planning_note("/plans", style="compact", levers=levers,
                                platform="mattermost" if variant == "mattermost" else "tui",
                                reply_plan=variant == "reply_plan")
    assert (text in note) is enabled
    if lever == "bridge_calls":
        assert ('tool_search "plan_mode"' in note) is not enabled
        if enabled:
            assert note.count(render.BRIDGE_SUBMIT) == 1
    if variant == "reply_plan":
        assert "In that same response" in note
    elif variant == "mattermost":
        assert "3–5 line summary plus the path" in note


@pytest.mark.parametrize("reply_plan", [False, True])
def test_042_lean_lookup_removed_and_classic_kept(make_plugin, reply_plan):
    for profile in ("lean", "classic"):
        plugin = make_plugin(plan_profile=profile)
        levers = plugin._note_levers() | ({"plan_once"} if reply_plan else set())
        note = render.planning_note("/plans", style="compact", levers=levers, reply_plan=reply_plan)
        assert ('tool_search "plan_mode"' in note) == (profile == "classic")
        assert (render.BRIDGE_SUBMIT in note) == (profile == "lean")


@pytest.mark.parametrize("sticky", [False, True])
def test_042_blocked_line_order(sticky):
    levers = {"blocked_tools", "skill_policy"} | ({"sticky"} if sticky else set())
    lines = render.planning_note("/plans", levers=levers).splitlines()
    index = lines.index(render.BLOCKED_TOOLS)
    assert lines[index - 1].startswith("While plan mode is on" if sticky else "In group chats")
    assert lines[index + 1] == render.SKILL_POLICY


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("batch", [False, True])
def test_042_execution_levers(make_plugin, enabled, batch):
    levers = ({"bridge_calls", "skill_policy"} if enabled else set()) | ({"todo_batch"} if batch else set())
    plugin = make_plugin(",".join(sorted(levers)), plan_profile="classic")
    hint = plugin._execute_hint()
    todo = pm._BATCH_TODO_HINT if batch else pm._TODO_HINT
    assert hint.startswith(todo + (render.BRIDGE_TODO if enabled else ""))
    assert (render.SKILL_EXECUTION in hint) is enabled
    pointer = render.executing_pointer({"approved_path": "/plans/p.md"}, levers=plugin._note_levers())
    assert (render.SKILL_EXECUTION in pointer) is enabled
    assert render.LEAN_EXECUTION not in pointer and render.LEAN_EXECUTION not in hint


@pytest.mark.parametrize("lever,text", [
    ("bridge_calls", render.LIGHT_BRIDGE),
    ("blocked_tools", render.LIGHT_BLOCKED),
    ("skill_policy", render.LIGHT_SKILL),
])
@pytest.mark.parametrize("enabled", [False, True])
def test_042_light_levers(make_plugin, lever, text, enabled):
    plugin = make_plugin("light_notes" + ("," + lever if enabled else ""), plan_profile="classic")
    plugin.command("on")
    assert "1. Explore" in plugin.pre_llm_call()["context"]
    state = plugin._load_state("sk:unit-session")
    # Use a short safe path so the sentence budget never masks an enabled lever.
    state["plans_dir"] = "/plans"
    plugin._save_state("sk:unit-session", state)
    note = plugin.pre_llm_call()["context"]
    assert note == render.light_note("/plans", levers=plugin._note_levers())
    assert (text in note) is enabled


def test_042_light_sentence_budget():
    extras = [render.LIGHT_BRIDGE, render.LIGHT_BLOCKED, render.LIGHT_SKILL]
    levers = pm.LEAN_LEVERS
    base = render.light_note("/plans")
    assert render.light_note("/plans", levers=levers) == base + "".join(extras)
    plans = "/" + "p" * 199
    base = render.light_note(plans)
    note = render.light_note(plans, levers=levers)
    assert len(note) <= 400
    assert note in [base + "".join(extras[:count]) for count in range(4)]
    assert note == base  # the bridge sentence cannot fit, so all later sentences are dropped
    for plans in ("/plans", "/" + "p" * 500):
        old = (f"Plan mode is ON. Plans directory: {plans}. Submit with plan_mode; "
               'do not ask "should I proceed?" in prose.')[:400]
        assert render.light_note(plans, levers={"light_notes"}) == old
        if len(plans) > 400:
            assert render.light_note(plans, levers=levers) == old


def test_042_bridge_examples_parse_registered_schema(make_plugin):
    plugin = make_plugin()
    note = render.planning_note("/plans", levers=plugin._note_levers())
    reply = render.planning_note("/plans", levers=plugin._note_levers() | {"plan_once"}, reply_plan=True)
    light = render.light_note("/plans", levers=plugin._note_levers())
    for text in (note, reply, light, plugin._execute_hint()):
        assert "tool_call(calls=" in text
        calls_json, rest = text.split("tool_call(calls=", 1)[1].split(")", 1)
        calls = json.loads(calls_json)
        assert len(calls) == 1
        call = calls[0]
        if call["name"] == "plan_mode":
            assert call["arguments"]["action"] == "submit"
            schema = plugin.ctx.tools["plan_mode"]["schema"]
            assert call["arguments"]["action"] in schema["parameters"]["properties"]["action"]["enum"]
        else:
            assert call["name"] == "todo_list"
            assert call["arguments"]["todos"][0] == {"id": "1", "content": "<step>", "status": "in_progress"}
            assert 'add "merge": true' in rest


@pytest.mark.parametrize("profile,levers,enabled", [
    ("lean", "", True), ("classic", "", False),
    ("classic", "blocked_tools", True), ("lean", "sticky", False),
])
@pytest.mark.parametrize("name", ["terminal", "execute_code", "delegate_task", "mcp_example", ""])
def test_042_catch_all_message_and_decisions(make_plugin, tmp_path, profile, levers, enabled, name):
    plugin = make_plugin(levers, plan_profile=profile)
    plugin.command("on")
    result = plugin.pre_tool_call(name, {})
    tool = name or "unknown tool"
    expected = (f"Plan mode is on: {tool} is blocked until the user approves the plan. Commands and tests run "
                "only after approval: put them in the plan's test plan and do not retry blocked tools." if enabled else
                f"Plan mode is on: {tool} is blocked until you approve the plan (/planmode approve). "
                "You may write the plan under .hermes/plans/.")
    assert result == {"action": "block", "message": expected}
    for read_only in pm.READ_ONLY_TOOLS:
        assert plugin.pre_tool_call(read_only, {}) is None
    assert plugin.pre_tool_call("plan_mode", {"action": "status"}) is None
    plans = plugin._load_state("sk:unit-session")["plans_dir"]
    assert plugin.pre_tool_call("write_file", {"path": str(Path(plans) / "p.md")}) is None
    refusal = plugin.pre_tool_call("write_file", {"path": str(tmp_path / "outside.md")})
    assert refusal["action"] == "block" and "Use only absolute paths" in refusal["message"]
    assert "do not retry" not in refusal["message"]


def test_042_catch_all_lever_failure_is_closed(make_plugin, monkeypatch):
    plugin = make_plugin()
    plugin.command("on")
    def broken():
        raise ValueError("bad lever config")
    monkeypatch.setattr(plugin, "_note_levers", broken)
    result = plugin.pre_tool_call("terminal", {})
    assert result["action"] == "block" and "failed closed (ValueError)" in result["message"]
    assert plugin.pre_tool_call("read_file", {}) is None


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("route", ["gate", "typed", "typed_open", "bypass", "clarify"])
def test_042_approval_paths(make_plugin, enabled, route):
    plugin = make_plugin("bridge_calls,skill_policy" if enabled else "approved_text", plan_profile="classic")
    path = write_plan(plugin)
    if route == "gate":
        message = approve_gate(plugin, path)["message"]
    elif route == "typed":
        plugin.command("approve")
        message = plugin._load_state("sk:unit-session")["pending_note"]
    elif route == "typed_open":
        plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
        plugin.command("approve")
        message = json.loads(plugin.tool({"action": "submit"}))["message"]
    else:
        plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
        result = json.loads(plugin.tool({"action": "submit"}))
        if route == "bypass":
            message = result["message"]
        else:
            clarify = result["clarify"]
            args = {"questions": [clarify]}
            assert plugin.pre_tool_call("clarify", args, tool_call_id="clarify") is None
            payload = json.dumps({"question": clarify["question"], "user_response": clarify["choices"][0]})
            plugin.post_tool_call("clarify", args, tool_call_id="clarify", status="ok", result=payload)
            message = json.loads(plugin.transform_tool_result("clarify", args, result=payload,
                                                             tool_call_id="clarify"))["plan_mode_approval"]
    assert (render.BRIDGE_TODO in message) is enabled
    assert (render.SKILL_EXECUTION in message) is enabled


@pytest.mark.parametrize("levers", ["skill_policy", "all"])
def test_042_plan_skill_suppresses_policy(make_plugin, monkeypatch, levers):
    monkeypatch.setattr(pm, "_skill_view_block_reason", lambda: None)
    plugin = make_plugin(levers, plan_skill="project-plan")
    assert "skill_policy" in plugin._levers() and "skill_policy" not in plugin._note_levers()
    path = write_plan(plugin)
    assert render.SKILL_POLICY not in plugin._planning_note({"plans_dir": str(path.parent)})
    assert render.SKILL_POLICY not in render.planning_note("/plans", plan_skill="project-plan", levers={"skill_policy"})
    assert render.SKILL_EXECUTION not in plugin._execute_hint()
    assert render.LIGHT_SKILL not in render.light_note("/plans", levers=plugin._note_levers())
    plugin.command("approve")
    assert render.SKILL_EXECUTION not in plugin._load_state("sk:unit-session")["pending_note"]
    assert render.SKILL_EXECUTION not in plugin.pre_llm_call()["context"]


@pytest.mark.parametrize("lever,text", [("test_commands", render.TEST_COMMANDS),
    ("choice_fidelity", render.CHOICE_FIDELITY), ("refactor_scope", render.REFACTOR_SCOPE.format(plans_dir="/plans"))])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("skill,question,reply,craft", list(itertools.product(("", "project-plan"), (False, True), (False, True), (False, True))))
def test_043_planning_levers(lever, text, enabled, skill, question, reply, craft):
    levers = (set(pm.LEAN_LEVERS) - NEW_044 - {"test_commands", "choice_fidelity", "refactor_scope", "lean_craft", "question_shape"})
    levers |= ({lever} if enabled else set()) | ({"question_shape"} if question else set())
    levers |= ({"plan_once"} if reply else set()) | ({"lean_craft"} if craft else set())
    note = render.planning_note("/plans", style="compact", plan_skill=skill, reply_plan=reply, levers=levers)
    assert (text in note) is enabled
    if enabled:
        if lever == "choice_fidelity":
            assert " " + text + "\n2. " in note
        else:
            assert note.index(text) < note.index("While plan mode is on")
            if craft and not skill:
                assert note.index("This supersedes the core") < note.index(text)
    if reply:
        assert "In that same response" in note
    full = render.planning_note("/plans", levers=set(pm.LEAN_LEVERS) | {"refactor_scope"})
    assert full.index(render.TEST_COMMANDS) < full.index(render.REFACTOR_SCOPE.format(plans_dir="/plans"))
    assert render.REFACTOR_SCOPE.format(plans_dir="/plans") not in render.planning_note("/plans", levers=pm.LEAN_LEVERS)


@pytest.mark.parametrize("inline,batch", list(itertools.product((False, True), repeat=2)))
def test_043_todo_inline(make_plugin, inline, batch):
    levers = ({"todo_inline"} if inline else set()) | ({"todo_batch"} if batch else set())
    plugin = make_plugin(",".join(levers), plan_profile="classic", allow_commits=True)
    expected = pm._INLINE_TODO_HINT if inline else pm._BATCH_TODO_HINT if batch else pm._TODO_HINT
    assert plugin._execute_hint() == expected
    pointer = render.executing_pointer({"approved_path": "/plans/p.md"}, levers=levers)
    text = "If using todo_list, never call it in a response by itself: send updates with your next edit or test call;"
    assert (text in pointer) is inline
    assert ("batch status updates" in pointer) is (batch and not inline)
    assert ("Keep todo_list statuses current;" in pointer) is (not inline and not batch)


@pytest.mark.parametrize("profile,levers,bridge", [("lean", "", True), ("classic", "", False),
    ("lean", "sticky", False), ("classic", "bridge_calls", True)])
def test_043_agent_bridge(make_plugin, profile, levers, bridge):
    plugin = make_plugin(levers, plan_profile=profile)
    assert len(render.AGENT_HINT_BRIDGE) <= 200
    assert plugin.ctx.sections["plan-mode.hint"]["content"] == (render.AGENT_HINT_BRIDGE if bridge else render.AGENT_HINT)
    expected, absent = (pm._AGENT_NOTE_BRIDGE, pm._AGENT_NOTE) if bridge else (pm._AGENT_NOTE, pm._AGENT_NOTE_BRIDGE)
    result = json.loads(plugin.tool({"action": "on"}))["message"]
    assert expected in result and absent not in result
    state = plugin._load_state("sk:unit-session")
    state["light_note_seen"] = False
    plugin._save_state("sk:unit-session", state)
    note = plugin.pre_llm_call()["context"]
    assert expected in note and absent not in note


def test_043_differential_notes():
    cases = json.loads((Path(__file__).parent / "fixtures/notes_042.json").read_text())
    assert len(cases) == 67
    for case in cases:
        kwargs = case["kwargs"]
        kwargs["levers"] = set(kwargs["levers"]) - {
            "test_commands", "choice_fidelity", "refactor_scope", "todo_inline"}
        if case["kind"] == "planning_note":
            kwargs["now"] = datetime(2026, 10, 11, 12, 34, 56)
        assert getattr(render, case["kind"])(**kwargs) == case["text"], case


def test_043_typed_approve_todo_hint_carries_the_lean_qualifier(make_plugin):
    lean = make_plugin()
    hint = lean._typed_todo_hint()
    assert hint.startswith(" Mirror steps into todo_list only when the plan has 4 or more steps.")
    assert hint.endswith(render.BRIDGE_TODO)
    lean.ctx.settings["levers"] = "bridge_calls,todo_batch"
    assert lean._typed_todo_hint() == " M" + pm._BATCH_TODO_HINT[1:] + render.BRIDGE_TODO
    lean.ctx.settings["levers"] = "bridge_calls"
    assert lean._typed_todo_hint() == render.BRIDGE_TODO
    assert make_plugin(plan_profile="classic")._typed_todo_hint() == ""


@pytest.mark.parametrize("allowed,dropped", [([], False), (["web_search"], False), (["terminal"], True),
                                             (["execute_code"], True), (["delegate_task"], True), (["mcp_github_read"], True)])
def test_043_blocked_tools_note_respects_extra_allowed_tools(make_plugin, allowed, dropped):
    plugin = make_plugin()
    plugin.ctx.settings["extra_allowed_tools"] = allowed
    assert ("blocked_tools" not in plugin._note_levers()) is dropped
    assert "blocked_tools" in plugin._levers()  # the lever set itself is unchanged; only the note text adapts


def test_043_on_result_names_extra_allowed_tools(make_plugin):
    plugin = make_plugin()
    plugin.ctx.settings["extra_allowed_tools"] = ["terminal", "web_search"]
    assert "Exception: this configuration allows terminal, web_search." in plugin.tool({"action": "on"})
    plain = make_plugin(plan_profile="classic")
    assert "Exception:" not in plain.tool({"action": "on"})


def test_043_todo_inline_alone_clears_at_turn_end(make_plugin):
    plugin = make_plugin("todo_inline")
    approve_gate(plugin, write_plan(plugin))
    plugin.post_llm_call(**IDS)
    assert plugin.pre_llm_call() is None


@pytest.mark.parametrize("lever,text", [("rename_scope", render.RENAME_SCOPE.format(plans_dir="/plans")),
    ("test_plan_only", render.TEST_PLAN_ONLY), ("choice_record", render.CHOICE_RECORD)])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("style,skill,question,reply", list(itertools.product(
    ("compact", "core"), ("", "project-plan"), (False, True), (False, True))))
def test_044_planning_levers(lever, text, enabled, style, skill, question, reply):
    levers = ({lever} if enabled else set()) | ({"question_shape"} if question else set()) | {"choice_fidelity"}
    levers |= {"plan_once"} if reply else set()
    note = render.planning_note("/plans", style=style, plan_skill=skill, reply_plan=reply, levers=levers)
    assert (text in note) is enabled
    if lever == "choice_record" and enabled:
        assert render.CHOICE_FIDELITY + " " + text + "\n2. " in note
    assert render.light_note("/plans", levers=levers) == render.light_note("/plans")


@pytest.mark.parametrize("commands,refactor", list(itertools.product((False, True), repeat=2)))
def test_044_scope_order_and_choice_record(commands, refactor):
    levers = NEW_044 | {"choice_fidelity"} | ({"test_commands"} if commands else set())
    levers |= {"refactor_scope"} if refactor else set()
    note = render.planning_note("/plans", levers=levers)
    rename, refactor_text = render.RENAME_SCOPE.format(plans_dir="/plans"), render.REFACTOR_SCOPE.format(plans_dir="/plans")
    assert render.CHOICE_FIDELITY + " " + render.CHOICE_RECORD + "\n2. " in note
    assert (rename in note) is not refactor
    assert (refactor_text in note) is refactor
    expected = ([render.TEST_COMMANDS] if commands else []) + ([] if refactor else [rename])
    expected += [render.TEST_PLAN_ONLY] + ([refactor_text] if refactor else [])
    assert note.endswith("\n" + "\n".join(expected))


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("lever,text", [("edit_batch", render.EDIT_BATCH), ("brief_final", render.BRIEF_FINAL)])
def test_044_execution_levers(make_plugin, lever, text, enabled):
    levers = {"lean_craft", "skill_policy"} | ({lever} if enabled else set())
    plugin = make_plugin(",".join(sorted(levers)), plan_profile="classic")
    for output in (plugin._execute_hint(), render.executing_pointer({"approved_path": "/plans/p.md"}, levers=levers)):
        assert (text in output) is enabled
        assert output.endswith(render.SKILL_EXECUTION + (" " + text if enabled else ""))
    assert text not in render.planning_note("/plans", levers=levers)


def test_044_execution_order(make_plugin):
    levers = pm.LEAN_LEVERS | NEW_044
    plugin = make_plugin(",".join(sorted(levers)))
    for output in (plugin._execute_hint(), render.executing_pointer({"approved_path": "/plans/p.md"}, levers=levers)):
        assert output.endswith(render.SKILL_EXECUTION + " " + render.EDIT_BATCH + " " + render.BRIEF_FINAL)


@pytest.mark.parametrize("commits", [False, True])
def test_045_default_lean(make_plugin, commits):
    """0.4.5 default: the two planning levers render; the execution levers stay off, so the execute hint is 0.4.3's."""
    cases = json.loads(NOTES_043.read_text())
    plugin = make_plugin(allow_commits=commits)
    plugin.ctx.settings.pop("levers", None)
    assert plugin._levers() == pm.LEAN_LEVERS and NEW_044 & plugin._levers() == {"rename_scope", "test_plan_only"}
    hint = next(c for c in cases if c["kind"] == "execute_hint" and c["kwargs"]["commits"] is commits)
    assert plugin._execute_hint() == hint["text"]
    note = render.planning_note("/plans", levers=plugin._levers())
    assert render.RENAME_SCOPE.format(plans_dir="/plans") in note and render.TEST_PLAN_ONLY in note
    assert render.CHOICE_RECORD not in note


NOTES_043 = Path(__file__).parent / "fixtures/notes_043.json"


def test_044_differential_notes():
    cases = [case for case in json.loads(NOTES_043.read_text()) if case["kind"] != "execute_hint"]
    assert len(cases) == 67
    for case in cases:
        kwargs = dict(case["kwargs"])
        kwargs["levers"] = (set(kwargs["levers"]) | NEW_044) - NEW_044
        if case["kind"] == "planning_note":
            kwargs["now"] = datetime(2026, 10, 11, 12, 34, 56)
        assert getattr(render, case["kind"])(**kwargs) == case["text"], case


@pytest.mark.parametrize("commits", [False, True])
def test_044_execute_hint_byte_identity(make_plugin, commits):
    """The v0.4.3 tree computed these strings (tests/tools/gen_notes_043.py); CI checkouts carry no tags to recompute them."""
    cases = [case for case in json.loads(NOTES_043.read_text()) if case["kind"] == "execute_hint"]
    assert sorted(case["kwargs"]["commits"] for case in cases) == [False, True]
    case = next(case for case in cases if case["kwargs"]["commits"] is commits)
    plugin = make_plugin(",".join(sorted(pm.LEAN_LEVERS - NEW_044)), allow_commits=commits)
    assert sorted(plugin._levers()) == case["kwargs"]["levers"]
    assert plugin._execute_hint() == case["text"]


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("route", ["gate", "typed", "typed_open", "bypass", "clarify"])
def test_044_execution_levers_on_every_approval_path(make_plugin, enabled, route):
    """edit_batch and brief_final alone reach the model on every approval route (clarify included, PR #25 review)."""
    plugin = make_plugin("edit_batch,brief_final" if enabled else "approved_text", plan_profile="classic")
    path = write_plan(plugin)
    if route == "gate":
        message = approve_gate(plugin, path)["message"]
    elif route == "typed":
        plugin.command("approve")
        message = plugin.pre_llm_call()["context"]
    elif route == "typed_open":
        plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
        plugin.command("approve")
        message = json.loads(plugin.tool({"action": "submit"}))["message"]
    else:
        plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
        result = json.loads(plugin.tool({"action": "submit"}))
        if route == "bypass":
            message = result["message"]
        else:
            clarify = result["clarify"]
            args = {"questions": [clarify]}
            assert plugin.pre_tool_call("clarify", args, tool_call_id="clarify") is None
            payload = json.dumps({"question": clarify["question"], "user_response": clarify["choices"][0]})
            plugin.post_tool_call("clarify", args, tool_call_id="clarify", status="ok", result=payload)
            message = json.loads(plugin.transform_tool_result("clarify", args, result=payload,
                                                             tool_call_id="clarify"))["plan_mode_approval"]
    assert (render.EDIT_BATCH in message) is enabled
    assert (render.BRIEF_FINAL in message) is enabled
