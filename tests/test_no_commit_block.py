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
    'git add . && git commit -m "x"', "git commit -am x", "cd x && git commit -m y",
    "git add -A\ngit commit -m z", "git -c commit.gpgsign=false commit -m x",
    "git push -u origin HEAD", "git push --force-with-lease", "/usr/bin/git commit -m x",
    'git -C "/my repo" commit -m x', 'git -c user.name="A B" commit -m x',
    "git --git-dir .git commit -m x", "git --work-tree /x commit -m x", "sudo git push",
    "GIT_AUTHOR_NAME=a git commit -m x", 'sh -c "cd r && git push"',
    "(cd r; git commit -m x)", "echo ok; git push", "./git push", "$(git push)",
    "zsh -c 'git push'", "env GIT_AUTHOR_NAME=a command git push", "exec git push",
    "nohup git push", "time git push", "git --namespace 'a b' push",
    'git --git-dir="my repo/.git" push', "git --work-tree=/x push",
])
def test_commit_and_push_blocked(executing, command):
    assert executing.pre_tool_call("terminal", {"command": command}) == {
        "action": "block", "message": render.NO_COMMIT_BLOCK,
    }


@pytest.mark.parametrize("command", [
    "git status", "git diff", "git log --grep commit", "git commit-tree abc",
    "git stash push", "echo hello",
    'grep -rn "git commit" .', 'rg "git push" README.md', 'echo "git commit"',
    "echo git commit", 'pytest -k "git push"', "pytest tests/test_git_commit.py",
    "git log | grep commit", "man git-commit", "git commit-tree x",
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
    "please push", "push when done", "push your changes", "push the code", "push to the remote",
    "push it", "go ahead and commit", "make a commit", "git push", "push now",
    "push after the tests pass", "ok, push.", "No problem, go ahead and commit",
    "Tests pass. Commit and push.", "Don't commit; push now", "skip commits, push now",
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
    "don't push", "do not push anything", "push-to-talk", "the commit message parser", "a commit-msg hook",
    "don't commit", "dont commit", "do not commit", "no commits", "without committing",
    "do not make any commits", "please don't make commits", "no need to commit", "avoid commits",
    "stop committing", "don't  commit", "Don't commit or push", "shouldn't commit",
    "cant commit", "wont push", "skip commits", "git push notifications",
])
def test_refusal_or_feature_name_keeps_guard(executing, message):
    executing.pre_llm_call(user_message=message)
    assert not executing._load_state("sk:unit-session").get("user_asked_commits")
    assert executing.pre_tool_call("terminal", {"command": "git commit -m x"})["action"] == "block"


@pytest.mark.parametrize("site", ["command", "planning-turn"])
@pytest.mark.parametrize("message,allowed", [
    ("please commit when done", True), ("please push", True), ("push when done", True),
    ("No problem, go ahead and commit", True), ("do not make any commits", False),
    ("Don't commit or push", False), ("add push notifications", False),
])
def test_request_before_approval(make_plugin, directive_lean, site, message, allowed):
    plugin = make_plugin()
    path = write_plan(plugin)
    if site == "command":
        plugin.command("on " + message)
    else:
        plugin.pre_llm_call(user_message=message)
    approve_gate(plugin, path)
    result = plugin.pre_tool_call("terminal", {"command": "git commit -m x"})
    assert (result is None) is allowed
    if not allowed:
        assert result["action"] == "block"


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


def test_non_terminal_command_argument_allowed(executing):
    assert executing.pre_tool_call("execute_code", {"command": "git commit -m x"}) is None


@pytest.mark.parametrize("profile", ["classic", "lean"])
@pytest.mark.parametrize("ask_to_push", [False, True])
def test_tui_request_and_command_side_done(make_plugin, directive_lean, session_env, profile, ask_to_push):
    session_env.update(HERMES_SESSION_KEY="commit-command", HERMES_SESSION_SOURCE="tui",
                       HERMES_UI_SESSION_ID="")
    plugin = make_plugin(plan_profile=profile)
    path = write_plan(plugin)
    session_env["HERMES_UI_SESSION_ID"] = "commit-tab"
    assert plugin.pre_tool_call("terminal", {"command": "git status"})["action"] == "block"
    session_env["HERMES_UI_SESSION_ID"] = ""
    plugin.command(f"show {path}")
    assert "Plan approved" in plugin.command(f"approve {path}")
    session_env["HERMES_UI_SESSION_ID"] = "commit-tab"
    assert plugin._load_state("ui:commit-tab")["phase"] == "executing"
    assert plugin.pre_tool_call("terminal", {"command": "git commit -m x"}) == {
        "action": "block", "message": render.NO_COMMIT_BLOCK,
    }
    if ask_to_push:
        plugin.pre_llm_call(user_message="please push")
        for command in ["git commit -m x", "git push"]:
            assert plugin.pre_tool_call("terminal", {"command": command}) is None
        assert plugin._load_state("sk:commit-command")["user_asked_commits"] is True
    session_env["HERMES_UI_SESSION_ID"] = ""
    assert plugin.command("done") == "The executing plan is done."
    for ui in ["", "commit-tab"]:
        session_env["HERMES_UI_SESSION_ID"] = ui
        for command in ["git commit -m x", "git push"]:
            assert plugin.pre_tool_call("terminal", {"command": command}) is None
    for key in ["sk:commit-command", "ui:commit-tab"]:
        state = plugin._load_state(key)
        assert "phase" not in state and "user_asked_commits" not in state


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


def test_builtin_craft_marker_partition_does_not_lift_guard(make_plugin, directive_lean, monkeypatch):
    plugin = make_plugin()
    path = write_plan(plugin)
    prompt = (_BUILTIN_PROMPT.partition("Task to plan:")[0] + "Task to plan:\nship it"
              + pm._PLAN_CRAFT_START + ". Commit and push when done.")
    # Start in an existing planning state to bypass core /plan's activation-time paragraph split.
    # Observe the split calls too: a blank-line fallback would otherwise mask a missing partition.
    splits = []
    original_split = pm.re.split
    def record_split(pattern, *args, **kwargs):
        splits.append(pattern)
        return original_split(pattern, *args, **kwargs)
    monkeypatch.setattr(pm.re, "split", record_split)
    plugin.pre_llm_call(user_message=prompt)
    assert r"\n\s*\n" not in splits
    assert not plugin._load_state("sk:unit-session").get("user_asked_commits")
    approve_gate(plugin, path)
    assert plugin.pre_tool_call("terminal", {"command": "git push"}) == {
        "action": "block", "message": render.NO_COMMIT_BLOCK,
    }


def test_idle_request_does_not_set_flag(make_plugin):
    plugin = make_plugin()
    assert plugin.pre_llm_call(user_message="please commit when done") is None
    assert not plugin._load_state("sk:unit-session").get("user_asked_commits")


@pytest.mark.parametrize("command", [
    "FOO=" + "a" * 5000 + " bar",
    "git -c " + "a" * 5000 + " status",
    "git --git-dir " + "a" * 5000 + " status",
    "git --x=" + "a" * 5000 + " status",
    'git -c "' + "a b " * 2000 + '" status',
])
def test_git_commit_pattern_stays_fast_on_long_tokens(command):
    """A nested quantifier here once took seconds per extra character, which would hang pre_tool_call."""
    import time

    start = time.monotonic()
    assert pm._runs_git_commit(command) is False
    assert time.monotonic() - start < 0.5


# Review round 2: forms the command-position regex missed, and data that only mentions git.
@pytest.mark.parametrize("command", [
    "if true; then git commit -m x; fi", "{ git commit -m x; }", "while true; do git push; done",
    "! git commit -m x", "git ls-files | xargs git commit -m x", "nice git push", "timeout 60 git push",
    "sudo -u bob git push", "env -i git push", "bash -lc 'git push'", 'sh -xc "git push"', "git -p commit -m x",
    "git -P push", "for f in a b; do git add $f; git commit -m $f; done", "eval 'git push'",
    "sudo bash -c 'git commit -m x'", "git commit -m 'unbalanced", "git add -A\r\ngit commit -m x",
])
def test_more_commit_forms_blocked(executing, command):
    assert executing.pre_tool_call("terminal", {"command": command}) == {
        "action": "block", "message": render.NO_COMMIT_BLOCK,
    }


@pytest.mark.parametrize("command", [
    'grep -c "git commit" log.txt', "rg -c 'git push' .", "git log --oneline | head", "git stash push -m wip",
    "git show HEAD:commit.txt", "python3 -m pytest -q -k commit", "git -C /repo status", "echo 'bash -c git push'",
])
def test_more_safe_commands_allowed(executing, command):
    assert executing.pre_tool_call("terminal", {"command": command}) is None


@pytest.mark.parametrize("message", [
    "push the new item onto the stack", "push to the array instead", "push the button left", "push my luck",
    "don’t commit", "please don’t commit or push", "nothing to commit",
])
def test_more_non_requests_keep_guard(executing, message):
    executing.pre_llm_call(user_message=message)
    assert not executing._load_state("sk:unit-session").get("user_asked_commits")
    assert executing.pre_tool_call("terminal", {"command": "git commit -m x"})["action"] == "block"


@pytest.mark.parametrize("message", ["push up the changes", "ok, push the branch", "commit this, then push it"])
def test_more_requests_lift_guard(executing, message):
    executing.pre_llm_call(user_message=message)
    assert executing.pre_tool_call("terminal", {"command": "git commit -m x"}) is None


@pytest.mark.parametrize("command", [
    "git " + "--git-dir " * 40 + "status", "\n" * 10000, ";" * 10000, "(" * 10000,
    "cat <<EOF" + "\n" * 2000 + "EOF", "curl 'http://x/?" + "a=1&" * 2500 + "'", "echo " + "\n\n  " * 3000,
    "bash -c " * 50 + "'echo hi'", "'" * 5001,
])
def test_commit_scan_stays_fast_on_adversarial_commands(command):
    import time

    start = time.monotonic()
    pm._runs_git_commit(command)
    assert time.monotonic() - start < 0.5


def test_request_scan_stays_fast_on_long_messages():
    import time

    start = time.monotonic()
    assert pm._user_asked_commits("don't commit " * 2000) is False
    assert time.monotonic() - start < 0.5


# Review round 3: a quote opened in prose must not hide a later commit; continuations; very long commands.
@pytest.mark.parametrize("command", [
    "cd repo  # it's here\ngit commit -m x  # that's it",
    "cat > a.md <<'EOF'\nDon't panic\nEOF\ngit add a.md && git commit -m wip\ncat > b.md <<'EOF'\nIt's fine\nEOF",
    "git -C repo \\\ncommit -m x", "git \\\ncommit -m x", "git commit\\\n -m x", "git push\\\n origin main",
    "echo '" + "a" * 30000 + "'\ngit push", "x=" + "a" * 30000 + "; git commit -m y",
])
def test_round3_commit_forms_blocked(executing, command):
    assert executing.pre_tool_call("terminal", {"command": command}) == {
        "action": "block", "message": render.NO_COMMIT_BLOCK,
    }


@pytest.mark.parametrize("command", [
    'git add docs/C#-guide.md && git commit -m "Update guide"', "echo a#b; git push", "git commit -m 'fix #12'",
])
def test_embedded_hash_never_hides_a_commit(executing, command):
    assert executing.pre_tool_call("terminal", {"command": command})["action"] == "block"


@pytest.mark.parametrize("message", [
    "push to my fork", "push to the fork", "push the PR", "push to a new branch", "push to feature/x",
    "push and open a PR", "push origin main", "push upstream", "push pls",
])
def test_round3_requests_lift_guard(executing, message):
    executing.pre_llm_call(user_message=message)
    assert executing.pre_tool_call("terminal", {"command": "git push"}) is None


@pytest.mark.parametrize("message", ["neither commit nor push", "hold off on committing", "refrain from committing"])
def test_round3_refusals_keep_guard(executing, message):
    executing.pre_llm_call(user_message=message)
    assert executing.pre_tool_call("terminal", {"command": "git commit -m x"})["action"] == "block"


@pytest.mark.parametrize("command", [
    "echo " + "a" * 1_000_000, "echo '" + "a" * 1_000_000, "echo 'git " + "a" * 1_000_000,
    ("echo 'git' " + "b" * 50 + "\n") * 20000, ";" * 100_000, "\n" * 100_000,
])
def test_round3_scan_stays_fast_on_megabyte_commands(command):
    import time

    start = time.monotonic()
    pm._runs_git_commit(command)
    assert time.monotonic() - start < 1.0
