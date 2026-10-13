"""Opt-in execution guard; exercise real planning/approval hooks with fake hosts."""
import json
from pathlib import Path

import pytest

from plan_mode import plugin as pm
from plan_mode import render
from test_levers import approve_gate, make_plugin, write_plan
from test_plan_mode import _BUILTIN_PROMPT, session_env


@pytest.fixture
def executing(make_plugin):
    plugin = make_plugin("exec_skill_guard")
    approve_gate(plugin, write_plan(plugin))
    assert plugin._load_state("sk:unit-session")["phase"] == "executing"
    return plugin


@pytest.mark.parametrize("name", [
    "test-driven-development", "software-development/test-driven-development",
    "superpowers:test-driven-development", " TDD ", "requesting-code-review",
    "code-review", "writing-plans", "plan", "planning", "brainstorming",
])
def test_process_skill_blocked(executing, name):
    result = executing.pre_tool_call("skill_view", {"name": name})
    assert result["action"] == "block"
    assert "approved plan is the process" in result["message"]
    assert name in result["message"]


@pytest.mark.parametrize("name", [
    "github", "systematic-debugging", "tdd-helper", "my-code-review", "planning-api", "",
])
def test_other_skills_allowed(executing, name):
    assert executing.pre_tool_call("skill_view", {"name": name}) is None


def test_default_lean_unchanged(make_plugin):
    plugin = make_plugin()
    approve_gate(plugin, write_plan(plugin))
    assert "exec_skill_guard" not in plugin._note_levers()
    assert plugin.pre_tool_call("skill_view", {"name": "test-driven-development"}) is None


@pytest.mark.parametrize("reason", [None, "host refuses skill writes while planning"])
def test_planning_matches_base(make_plugin, monkeypatch, reason):
    monkeypatch.setattr(pm, "_skill_view_block_reason", lambda: reason)
    plugin = make_plugin("exec_skill_guard")
    plugin.command("on")
    assert plugin._load_state("sk:unit-session")["active"]
    guarded = plugin.pre_tool_call("skill_view", {"name": "test-driven-development"})
    plugin.ctx.settings["levers"] = ""
    assert guarded == plugin.pre_tool_call("skill_view", {"name": "test-driven-development"})


@pytest.mark.parametrize("message,asked", [
    ("Please use TDD for this", True), ("add a --json flag", False),
    ("use test-driven development", True), ("red/green please", True),
    ("red-first", True), ("tests first", True), ("code-review it", True),
])
def test_request_before_approval(make_plugin, message, asked):
    plugin = make_plugin("exec_skill_guard")
    path = write_plan(plugin)
    plugin.pre_llm_call(user_message=message)
    assert bool(plugin._load_state("sk:unit-session").get("user_asked_process")) is asked
    approve_gate(plugin, path)
    result = plugin.pre_tool_call("skill_view", {"name": "test-driven-development"})
    assert (result is None) is asked


def test_request_after_approval_persists(executing):
    executing.pre_llm_call(user_message="ok, do it test-first")
    executing.pre_llm_call(user_message="continue")
    assert executing._load_state("sk:unit-session")["user_asked_process"] is True
    assert executing.pre_tool_call("skill_view", {"name": "test-driven-development"}) is None


@pytest.mark.parametrize("task,asked", [("ship it", False), ("use TDD", True)])
@pytest.mark.parametrize("enforce", [True, False])
def test_builtin_wrapper_only_matches_task(make_plugin, task, asked, enforce):
    plugin = make_plugin("exec_skill_guard", enforce_builtin_plan=enforce)
    if not enforce:
        plugin.command("on")
    message = _BUILTIN_PROMPT.replace("ship it", task).replace(
        "Offer execution later.", "Use test-driven development and code-review later.")
    plugin.pre_llm_call(user_message=message)
    assert bool(plugin._load_state("sk:unit-session").get("user_asked_process")) is asked


@pytest.mark.parametrize("user_text,asked", [("continue", False), ("Please use TDD", True)])
def test_pending_notes_do_not_count_as_request(make_plugin, user_text, asked):
    plugin = make_plugin("exec_skill_guard skill_policy lean_craft")
    path = write_plan(plugin)
    assert "Plan approved" in plugin.command(f"approve {path}")
    pending = plugin._load_state("sk:unit-session")["pending_note"]
    assert "TDD" in pending
    plugin.pre_llm_call(user_message=pending + "\n\n" + user_text)
    result = plugin.pre_tool_call("skill_view", {"name": "test-driven-development"})
    assert (result is None) is asked


@pytest.mark.parametrize("skill_loadable", [True, False])
def test_plan_skill_disables_guard(make_plugin, monkeypatch, skill_loadable):
    # A plan skill only takes effect when skill_view may load it; otherwise plan-mode falls back to Plan style and the
    # guard stays on. Pinned here so the no-Hermes CI job (no skill config loader) tests the same branch.
    monkeypatch.setattr(pm, "_skill_view_block_reason", lambda: None if skill_loadable else "inline shell enabled")
    plugin = make_plugin("exec_skill_guard", plan_skill="custom")
    approve_gate(plugin, write_plan(plugin))
    assert ("exec_skill_guard" in plugin._note_levers()) is not skill_loadable
    result = plugin.pre_tool_call("skill_view", {"name": "test-driven-development"})
    assert (result is None) is skill_loadable


@pytest.mark.parametrize("failure", ["name", "lookup", "levers", "message"])
def test_guard_failure_allows_call(executing, monkeypatch, failure):
    def fail(*args, **kwargs):
        raise RuntimeError("guard fault")

    class BadName:
        __str__ = fail

    class BadSkills:
        __contains__ = fail

    class BadMessage:
        format = fail

    args = {"name": "test-driven-development"}
    if failure == "name":
        args["name"] = BadName()
    elif failure == "lookup":
        monkeypatch.setattr(pm, "EXEC_PROCESS_SKILLS", BadSkills(), raising=False)
    elif failure == "levers":
        monkeypatch.setattr(executing, "_note_levers", fail)
    else:
        monkeypatch.setattr(pm, "EXEC_SKILL_GUARD_MESSAGE", BadMessage(), raising=False)
    assert executing.pre_tool_call("skill_view", args) is None


def test_completion_clears_flag_and_guard(executing):
    assert "user_asked_process" in pm._EXECUTION_FIELDS
    executing.pre_llm_call(user_message="Please use TDD")
    state = executing._load_state("sk:unit-session")
    assert state["user_asked_process"] is True
    todos = [{"id": "1", "content": "work", "status": "completed"}]
    executing.post_tool_call("todo_list", {"todos": todos}, status="ok", result=json.dumps({"todos": todos}))
    state = executing._load_state("sk:unit-session")
    assert "user_asked_process" not in state
    assert "phase" not in state
    assert executing.pre_tool_call("skill_view", {"name": "test-driven-development"}) is None
    approve_gate(executing, write_plan(executing))
    assert executing.pre_tool_call("skill_view", {"name": "test-driven-development"})["action"] == "block"


@pytest.mark.parametrize("tool", ["terminal", "write_file", "todo_list"])
def test_other_tools_allowed_without_state_writes(executing, monkeypatch, tool):
    def no_writes(*args, **kwargs):
        pytest.fail("execution guard wrote state")
    monkeypatch.setattr(executing, "_save_state", no_writes)
    assert executing.pre_tool_call(tool, {"name": "test-driven-development"}) is None


@pytest.mark.parametrize("phase", [None, "planning", "finished"])
def test_nonexecuting_phase_allowed(make_plugin, phase):
    plugin = make_plugin("exec_skill_guard")
    plugin._save_state("sk:unit-session", {"active": False, "phase": phase})
    assert plugin.pre_tool_call("skill_view", {"name": "test-driven-development"}) is None


def test_guard_has_no_state_writes(executing, monkeypatch):
    def no_writes(*args, **kwargs):
        pytest.fail("execution guard wrote state")
    monkeypatch.setattr(executing, "_save_state", no_writes)
    assert executing.pre_tool_call("skill_view", {"name": "test-driven-development"})["action"] == "block"


def test_lever_registration(make_plugin, monkeypatch):
    assert "exec_skill_guard" in pm._LEVERS
    assert "exec_skill_guard" not in pm.LEAN_LEVERS
    assert "exec_skill_guard" in make_plugin("all")._levers()
    plugin = make_plugin()
    monkeypatch.setenv("PLAN_MODE_LEVERS", "exec_skill_guard")
    assert plugin._levers() == {"exec_skill_guard"}


def test_message_short():
    assert len(render.EXEC_SKILL_GUARD_MESSAGE) < 300


@pytest.mark.parametrize("levers,filename", [
    ("exec_skill_guard,commit_override", "parser.md"),
    ("exec_skill_guard", "add-tdd-coverage.md"),
    ("exec_skill_guard", "code-review-parser.md"),
])
def test_fix1_injected_implementation_is_not_user_request(make_plugin, levers, filename):
    plugin = make_plugin(levers)
    plugin.command("on parser")
    path = Path(plugin._load_state("sk:unit-session")["plans_dir"]) / filename
    assert plugin.pre_tool_call("write_file", {"path": str(path)}) is None
    path.write_text("# Plan\n\nChange parser.py; run pytest.", encoding="utf-8")
    assert "Plan approved" in plugin.command("approve")
    injected = plugin.ctx.injected[-1][0]
    plugin.pre_llm_call(user_message=" \n" + injected)
    assert not plugin._load_state("sk:unit-session").get("user_asked_process")
    assert plugin.pre_tool_call("skill_view", {"name": "test-driven-development"})["action"] == "block"


def test_fix2_flag_save_failure_preserves_planning_note(make_plugin, monkeypatch):
    plugin = make_plugin("exec_skill_guard")
    write_plan(plugin)
    expected = plugin.pre_llm_call(user_message="add a --json flag")
    assert "files may be written only under" in expected["context"]
    save = plugin._save_state
    attempts = []

    def fail_flag_write(key, state):
        if state.get("user_asked_process"):
            attempts.append(key)
            raise OSError("flag write fault")
        return save(key, state)

    monkeypatch.setattr(plugin, "_save_state", fail_flag_write)
    assert plugin.pre_llm_call(user_message="please use TDD") == expected
    assert attempts == ["sk:unit-session"]


def test_fix2_idle_message_does_not_set_flag(make_plugin):
    plugin = make_plugin("exec_skill_guard")
    plugin._save_state("sk:unit-session", {"active": False})
    assert plugin.pre_llm_call(user_message="please use TDD") is None
    assert "user_asked_process" not in plugin._load_state("sk:unit-session")


def test_fix3_message_describes_tdd_and_review_escape():
    assert render.EXEC_SKILL_GUARD_MESSAGE == (
        "Not loaded: {name}. The approved plan is the process for this task. Make each todo's edits together, "
        "then run the tests once. If the user asks for TDD or a code review, those skills will be allowed."
    )


@pytest.mark.parametrize("name", ["writing-plans", "plan", "planning", "brainstorming"])
def test_fix3_process_request_keeps_planning_skills_blocked(executing, name):
    executing.pre_llm_call(user_message="please use TDD and code review")
    assert executing.pre_tool_call("skill_view", {"name": "tdd"}) is None
    assert executing.pre_tool_call("skill_view", {"name": "requesting-code-review"}) is None
    assert executing.pre_tool_call("skill_view", {"name": name})["action"] == "block"


@pytest.mark.parametrize("entered_by,asked", [("user", True), ("agent", False)])
def test_fix4_on_task_process_request(make_plugin, entered_by, asked):
    plugin = make_plugin("exec_skill_guard")
    plugin._run_command("on", "add a --json flag using TDD", entered_by=entered_by)
    state = plugin._load_state("sk:unit-session")
    assert bool(state.get("user_asked_process")) is asked
    path = Path(state["plans_dir"]) / "parser.md"
    assert plugin.pre_tool_call("write_file", {"path": str(path)}) is None
    path.write_text("# Plan\n\nChange parser.py; run pytest.", encoding="utf-8")
    approve_gate(plugin, path)
    assert (plugin.pre_tool_call("skill_view", {"name": "tdd"}) is None) is asked


@pytest.mark.parametrize("message", [
    ["please use TDD"],
    [{"type": "text", "text": "please use test-first"}],
    ["please use ", {"text": "code review"}, {"type": "image", "text": None}, 7],
])
def test_fix4_multimodal_process_request(executing, message):
    executing.pre_llm_call(user_message=message)
    assert executing._load_state("sk:unit-session")["user_asked_process"] is True
    assert executing.pre_tool_call("skill_view", {"name": "tdd"}) is None


@pytest.mark.parametrize("name", ["superpowers: tdd", "software-development/test-driven-development/"])
def test_fix5_normalized_names_blocked(executing, name):
    assert executing.pre_tool_call("skill_view", {"name": name})["action"] == "block"
