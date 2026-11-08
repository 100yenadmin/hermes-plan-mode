"""Terminal commit/push guard through real approval hooks and disposable hosts."""
import json

import pytest

from plan_mode import plugin as pm
from plan_mode import render
from test_approval_card import answer_card, submit
from test_levers import approve_gate, make_plugin, write_plan
from test_plan_mode import _BUILTIN_PROMPT, session_env


@pytest.fixture(params=["default-lean", "directive-lean", "classic"])
def executing(request, make_plugin, monkeypatch):
    profile = request.param
    if profile == "directive-lean":
        request.getfixturevalue("directive_lean")
    plugin = make_plugin(**({"plan_profile": "classic"} if profile == "classic" else {}))
    path = write_plan(plugin)
    if profile == "default-lean":
        assert "approval_card" in plugin._levers()
        monkeypatch.setattr(pm, "_edit_review_available", lambda: True)
        answer_card(plugin, submit(plugin, path), "Approve, review each file edit")
        assert isinstance(plugin._load_state("sk:unit-session")["edit_review"], dict)
    else:
        approve_gate(plugin, path)
    state = plugin._load_state("sk:unit-session")
    assert state["phase"] == "executing" and not state["active"]
    return plugin


@pytest.mark.parametrize("command", [
    "git commit -m step", "git add -A && git commit -m x", "git -c user.name=a commit -m x",
    "git -C /repo commit -am x", "git push", "bash -c 'git commit -m x'",
    "git --no-pager commit -m x", "git status;git push", "git status|git push",
    "(git push)", '`git push`', 'bash -c "git push"',
])
def test_commit_and_push_blocked(executing, command):
    assert executing.pre_tool_call("terminal", {"command": command}) == {
        "action": "block", "message": render.NO_COMMIT_BLOCK,
    }


@pytest.mark.parametrize("command", [
    "git status", "git diff", "git log --grep commit", "git commit-tree abc",
    "git stash push", "echo hello",
])
def test_other_commands_allowed(executing, command):
    assert executing.pre_tool_call("terminal", {"command": command}) is None


@pytest.mark.parametrize("command", ["git commit -m x", "git push"])
def test_allow_commits_on(executing, command):
    executing.ctx.settings["allow_commits"] = True
    assert executing.pre_tool_call("terminal", {"command": command}) is None


@pytest.mark.parametrize("message", [
    "please commit when done", "commit it", "commit and push", "git push when done",
    "push it to origin", "push the changes", "push to origin", "commits are fine",
])
def test_explicit_request_lifts_guard(executing, message):
    executing.pre_llm_call(user_message=message)
    executing.pre_llm_call(user_message="continue")
    assert executing._load_state("sk:unit-session")["user_asked_commits"] is True
    for command in ["git commit -m x", "git push"]:
        assert executing.pre_tool_call("terminal", {"command": command}) is None


@pytest.mark.parametrize("message", [
    "don't commit anything", "do not commit anything", "no commits please", "never commit",
    "finish without committing", "add push notifications", "fix push-to-talk",
    "Commit message parser", "add a commit-msg hook",
])
def test_refusal_or_feature_name_keeps_guard(executing, message):
    executing.pre_llm_call(user_message=message)
    assert not executing._load_state("sk:unit-session").get("user_asked_commits")
    assert executing.pre_tool_call("terminal", {"command": "git commit -m x"})["action"] == "block"


@pytest.mark.parametrize("site", ["command", "planning-turn"])
def test_request_before_approval(make_plugin, directive_lean, site):
    plugin = make_plugin()
    path = write_plan(plugin)
    if site == "command":
        plugin.command("on please commit when done")
    else:
        plugin.pre_llm_call(user_message="please commit when done")
    approve_gate(plugin, path)
    assert plugin.pre_tool_call("terminal", {"command": "git commit -m x"}) is None


@pytest.mark.parametrize("ending", ["done", "todos"])
def test_completion_clears_execution_and_request(executing, ending):
    executing.pre_llm_call(user_message="please commit when done")
    assert executing._load_state("sk:unit-session")["user_asked_commits"] is True
    if ending == "done":
        assert executing.command("done") == "The executing plan is done."
    else:
        todos = [{"id": "1", "content": "work", "status": "completed"}]
        executing.post_tool_call("todo_list", {"todos": todos}, status="ok", result=json.dumps({"todos": todos}))
    state = executing._load_state("sk:unit-session")
    assert "phase" not in state and "user_asked_commits" not in state
    assert executing.pre_tool_call("terminal", {"command": "git commit -m x"}) is None
    path = write_plan(executing)
    executing.command(f"show {path}")
    assert "Plan approved" in executing.command(f"approve {path}")
    assert executing.pre_tool_call("terminal", {"command": "git commit -m x"})["action"] == "block"


def test_planning_block_unchanged(make_plugin):
    plugin = make_plugin()
    plugin.command("on please commit when done")
    expected = plugin.pre_tool_call("terminal", {"command": "git status"})
    assert expected["action"] == "block" and "until" in expected["message"]
    assert plugin.pre_tool_call("terminal", {"command": "git commit -m x"}) == expected
    assert expected["message"] != render.NO_COMMIT_BLOCK


@pytest.mark.parametrize("command", [None, 42, ["git", "commit"], {"command": "git push"}])
def test_non_string_command_fails_open(executing, command):
    assert executing.pre_tool_call("terminal", {"command": command}) is None


def test_config_failure_fails_open(executing, monkeypatch):
    def fail():
        raise RuntimeError("config fault")
    monkeypatch.setattr(executing, "_allow_commits", fail)
    assert executing.pre_tool_call("terminal", {"command": "git commit -m x"}) is None


def test_injected_implementation_does_not_lift_guard(executing):
    assert executing._start_implementation(executing._load_state("sk:unit-session")["approved_path"])
    injected = executing.ctx.injected[-1][0]
    assert injected.startswith(pm._IMPLEMENT_PREFIX)
    executing.pre_llm_call(user_message=" \n" + injected)
    assert not executing._load_state("sk:unit-session").get("user_asked_commits")
    assert executing.pre_tool_call("terminal", {"command": "git push"})["action"] == "block"


def test_builtin_craft_text_does_not_lift_guard(make_plugin):
    plugin = make_plugin()
    plugin.pre_llm_call(user_message=_BUILTIN_PROMPT.replace("Offer execution later.", "Commit when done."))
    assert not plugin._load_state("sk:unit-session").get("user_asked_commits")


def test_idle_request_does_not_set_flag(make_plugin):
    plugin = make_plugin()
    assert plugin.pre_llm_call(user_message="please commit when done") is None
    assert not plugin._load_state("sk:unit-session").get("user_asked_commits")
