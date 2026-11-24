"""The stop gate can ask for one more pass on an unfinished approved plan."""
import json

import pytest

from plan_mode import plugin as pm
from test_plan_mode import executing_plan, plan_profile, plugin, session_env, submitted_plan  # noqa: F401 (fixtures)

pytestmark = pytest.mark.usefixtures("directive_lean")


def observe_todos(plugin, *steps):
    todos = [{"status": status, "content": content} for status, content in steps]
    plugin.post_tool_call("todo_list", {"todos": todos}, status="ok", result=json.dumps({"todos": todos}))


def finish(plugin, **overrides):
    args = dict(attempt=0, final_response="I made the first edit.", changed_paths=["app.py"], platform="cli")
    return plugin.pre_verify(**(args | overrides))


@pytest.fixture
def unfinished(plugin, executing_plan):
    observe_todos(plugin, ("completed", "Read"), ("cancelled", "Drop"), ("pending", "Test"), ("in_progress", "Build"))
    return plugin


def test_finish_hold_names_the_current_step_and_offers_an_out_once(unfinished):
    assert finish(unfinished) == {
        "action": "continue",
        "message": (
            "Plan rev 1 is not finished: 2 of 4 steps done, next: Build. Continue with it.\n"
            "If it is blocked, needs the user's decision, is waiting on a running process or a reply, or the user "
            "asked you to stop or to do something else, say so in one line and stop; if a step is no longer needed, "
            "mark it cancelled in todo_list and say why."
        ),
    }
    assert finish(unfinished, attempt=1) is None


def test_finish_hold_names_the_first_pending_step_without_a_current_step(unfinished):
    observe_todos(unfinished, ("completed", "Build"), ("pending", "Test"), ("pending", "Document"))
    assert "1 of 3 steps done, next: Test. Continue with it." in finish(unfinished)["message"]


def test_finish_hold_handles_older_progress_without_a_step_name(plugin, executing_plan):
    state = plugin._load_state("sk:unit-session")
    state["progress"] = {"total": 2, "completed": 1, "cancelled": 0, "current": ""}
    plugin._save_state("sk:unit-session", state)
    assert "next: the first pending step." in finish(plugin)["message"]


@pytest.mark.parametrize("overrides", [
    {"final_response": "[SILENT]"}, {"final_response": "Saved.\n\nNO_REPLY"},
    {"platform": "subagent"}, {"platform": " Curator "}, {"changed_paths": []},
])
def test_finish_hold_noops_for_silence_child_platforms_and_no_edits(unfinished, overrides):
    assert finish(unfinished, **overrides) is None


def test_finish_hold_noops_for_a_delegated_child_with_the_parent_platform(unfinished, monkeypatch):
    monkeypatch.setattr(pm, "_delegated_child", lambda: True)
    assert finish(unfinished) is None


@pytest.mark.parametrize("disabled", ["off", False])
def test_finish_hold_reads_the_setting_on_each_call(unfinished, disabled):
    unfinished.ctx.settings["finish_hold"] = disabled
    assert finish(unfinished) is None
    unfinished.ctx.settings["finish_hold"] = "on"
    assert finish(unfinished)["action"] == "continue"


def test_finish_hold_noops_while_finish_is_pending(unfinished):
    state = unfinished._load_state("sk:unit-session")
    state["finish_pending"] = True
    unfinished._save_state("sk:unit-session", state)
    assert finish(unfinished) is None


def test_finish_hold_noops_after_the_last_todo_is_cancelled(unfinished):
    observe_todos(unfinished, ("completed", "Build"), ("cancelled", "Document"))
    assert finish(unfinished) is None


def test_finish_hold_noops_with_no_todos(plugin, executing_plan):
    assert finish(plugin) is None
    observe_todos(plugin)
    assert finish(plugin) is None


@pytest.mark.parametrize("command", ["on", "off"])
def test_finish_hold_noops_after_execution_exits(unfinished, command):
    unfinished.command(command)
    assert finish(unfinished) is None


@pytest.mark.parametrize("session_key", ["other-session", ""])
def test_finish_hold_noops_for_another_session_or_unresolved_identity(unfinished, session_env, session_key):
    session_env["HERMES_SESSION_KEY"] = session_key
    assert finish(unfinished, platform="telegram") is None


def test_finish_hold_fails_open_when_the_state_loader_raises(unfinished, monkeypatch):
    def broken_loader(_key):
        raise RuntimeError("state unavailable")

    monkeypatch.setattr(unfinished, "_load_state", broken_loader)
    assert finish(unfinished) is None


def test_finish_hold_noops_right_after_the_user_denied_an_edit(unfinished):
    # The denial note reaches the model only on the next turn; holding now would contradict the user's "no".
    state = unfinished._load_state("sk:unit-session")
    state["edit_review"] = {"denied": True}
    unfinished._save_state("sk:unit-session", state)
    assert finish(unfinished) is None
    state["edit_review"] = {"denied": False}
    unfinished._save_state("sk:unit-session", state)
    assert finish(unfinished) is not None  # the same state without a denial still holds


def test_finish_hold_puts_a_multi_line_step_on_one_line(unfinished):
    observe_todos(unfinished, ("in_progress", "line1\nIGNORE ALL\n  line3"), ("pending", "Test"))
    message = finish(unfinished)["message"]
    assert "next: line1 IGNORE ALL line3. Continue with it." in message and message.count("\n") == 1
