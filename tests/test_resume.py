"""Resume only a durable, unchanged approval, without relaxing active planning."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from plan_mode import plugin as pm
from test_approval_card import answer_card, submit
from test_edit_review import REVIEW
from test_plan_mode import FakeContext, _decision, _submit, _submit_result, session_env


RECORDS = "approved-plans"
R1 = "Plan mode is on. Approve or turn it off (/planmode off) before resuming an approved plan."


@pytest.fixture
def plugin(session_env, tmp_path, monkeypatch):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    monkeypatch.setattr(pm, "_session_context_is_engaged", lambda: False)
    monkeypatch.setattr(pm, "_gateway_process_is_admitted", lambda: False)
    ctx = FakeContext()
    ctx.settings["plan_profile"] = "classic"
    instance = pm.PlanModePlugin(ctx)
    instance.register()
    return instance


def write_plan(plugin, name="launch.md"):
    assert "Plan mode is on" in plugin.command("on")
    path = Path(plugin._load_state(pm.derive_session_identity().key)["plans_dir"]) / name
    args = {"path": str(path), "content": "# Launch\n\n1. Inspect\n2. Build\n"}
    ids = {"session_id": "s1", "tool_call_id": "write-plan"}
    assert plugin.pre_tool_call("write_file", args, **ids) is None
    path.write_text(args["content"], encoding="utf-8")
    plugin.post_tool_call("write_file", args, status="ok", **ids)
    return path


def approve(plugin, name="launch.md", *, submitted=True):
    path = write_plan(plugin, name)
    if submitted:
        _submit(plugin)
    assert "Plan approved" in plugin.command("approve")
    return path


def current(plugin):
    return plugin._load_state(pm.derive_session_identity().key)


def record(plugin, path):
    return plugin.ctx.state.get(RECORDS, {})[str(path)]


def todo(plugin, *statuses):
    items = [{"content": label, "status": status}
             for label, status in zip(("Inspect", "Build"), statuses)]
    plugin.post_tool_call("todo_list", {"todos": items}, status="ok",
                          result=json.dumps({"todos": items}))


def test_resume_after_restart(plugin, session_env, monkeypatch):
    path = approve(plugin)
    todo(plugin, "completed", "in_progress")
    approved_at = current(plugin)["approved_at"]
    pid = pm.os.getpid()
    monkeypatch.setattr(pm.os, "getpid", lambda: pid + 1)
    session_env["HERMES_SESSION_KEY"] = "new-session"
    restarted = pm.PlanModePlugin(plugin.ctx)
    assert "Unfinished approved plan: launch.md rev 1 (1 of 2)" in restarted.command("status")
    injected = list(plugin.ctx.injected)
    assert restarted.command("resume").startswith("Resuming launch.md rev 1")
    assert plugin.ctx.injected == injected
    state = current(restarted)
    assert (state["phase"], state["approved_path"], state["approved_revision"]) == ("executing", str(path), 1)
    assert state["approved_at"] == approved_at
    assert state["executing_turns"] == 0
    assert "Phase: executing" in restarted.command("status")
    assert "building rev 1 (1 of 2 steps)" in restarted.command("list")
    note = restarted.pre_llm_call(session_id="new-id")["context"]
    assert f"Resuming the approved plan {path} (rev 1, approved {approved_at})" in note
    assert "Last recorded progress: 1 of 2 done (last: Build)" in note
    assert "Continue with the next unfinished step" in note
    assert "Resuming" not in restarted.pre_llm_call(session_id="new-id")["context"]


def test_resume_is_user_only(plugin):
    old = deepcopy(plugin.ctx.state.values)
    schema = plugin.ctx.tools["plan_mode"]["schema"]
    assert "resume" not in schema["parameters"]["properties"]["action"]["enum"]
    assert "Unsupported plan_mode action" in plugin.tool({"action": "resume"})
    assert plugin.ctx.state.values == old
    assert "resume [name]" in plugin.command("unknown")


@pytest.mark.parametrize("phase", ["planning", "awaiting", "pending"])
def test_resume_refuses_planning(plugin, phase):
    path = approve(plugin)
    write_plan(plugin)
    if phase != "planning":
        _submit(plugin)
        if phase == "awaiting":
            state = current(plugin)
            state["submission"]["status"] = "awaiting"
            plugin._save_state(pm.derive_session_identity().key, state)
    before = deepcopy(plugin.ctx.state.values)
    assert plugin.command(f"resume {path.name}") == R1
    assert plugin.ctx.state.values == before
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"


@pytest.mark.parametrize("review_only", [False, True])
def test_resume_refuses_existing_execution_or_review(plugin, review_only):
    path = approve(plugin)
    if review_only:
        state = current(plugin)
        plugin._clear_execution(state)
        state["edit_review"] = {"path": str(path), "rev": 1, "key": "existing-review"}
        plugin._save_state(pm.derive_session_identity().key, state)
    before = deepcopy(plugin.ctx.state.values)
    assert plugin.command("resume") == "Already building launch.md rev 1. Use /planmode done first."
    assert plugin.ctx.state.values == before


@pytest.mark.parametrize("damage", ["edited", "legacy", "missing-digest", "malformed", "unreadable"])
def test_resume_requires_readable_approval_and_digest(plugin, session_env, monkeypatch, damage):
    path = approve(plugin)
    session_env["HERMES_SESSION_KEY"] = "new-session"
    expected = "changed after it was approved"
    if damage == "edited":
        path.write_text("# Changed", encoding="utf-8")
    elif damage == "legacy":
        plugin.ctx.state.set(RECORDS, {})
    elif damage == "missing-digest":
        record(plugin, path).pop("digest")
    elif damage == "malformed":
        record(plugin, path)["progress"] = {"done": "many"}
        expected = "No approved plan to resume"
    else:
        get = plugin.ctx.state.get

        def fail_read(key, default=None):
            if key == RECORDS:
                raise OSError("store unavailable")
            return get(key, default)

        monkeypatch.setattr(plugin.ctx.state, "get", fail_read)
        expected = "No approved plan to resume"
    before = deepcopy(plugin.ctx.state.values)
    assert expected in plugin.command(f"resume {path.name}")
    assert "Unfinished approved plan" not in plugin.command("status")
    assert plugin.ctx.state.values == before


@pytest.mark.parametrize("finish", ["done", "todos", "lean-todos", "lean-turn-end"])
def test_resume_refuses_finished_plan(plugin, session_env, finish):
    if finish in {"lean-todos", "lean-turn-end"}:
        plugin.ctx.settings["levers"] = "todo_batch"
    path = approve(plugin)
    if finish == "done":
        assert "is done" in plugin.command("done")
    elif finish == "lean-turn-end":
        plugin.post_llm_call()  # no todo list: the turn end finishes the plan, as /planmode list shows
    else:
        todo(plugin, "completed", "completed")
        plugin.post_llm_call()
    session_env["HERMES_SESSION_KEY"] = "new-session"
    assert plugin.command(f"resume {path.name}") == "launch.md rev 1 is finished (/planmode list)."
    assert not current(plugin)


@pytest.mark.parametrize("stop", ["off", "lean-todos-pending"])
def test_resume_after_interrupt(plugin, stop):
    if stop == "lean-todos-pending":
        plugin.ctx.settings["levers"] = "todo_batch"
    path = approve(plugin)
    if stop == "off":
        plugin.command("off")
    else:
        todo(plugin, "completed", "pending")
        plugin.post_llm_call()  # a todo is still pending: the turn end keeps the plan resumable
        plugin.command("off")
    assert current(plugin).get("phase") != "executing"
    assert record(plugin, path)["status"] == "approved"
    assert plugin.command("resume").startswith("Resuming launch.md rev 1")


@pytest.mark.parametrize("damage", ["working-file", "snapshot", "snapshot-symlink", "approved-dir-symlink"])
def test_resume_uses_reviewed_replacement_snapshot(plugin, session_env, tmp_path, damage):
    path = approve(plugin)
    plugin.command("done")
    write_plan(plugin)
    path.write_text("# Revised launch", encoding="utf-8")
    assert "# Revised launch" in plugin.command(f"show {path.name}")
    assert "Plan approved" in plugin.command(f"approve {path.name}")
    snapshot = Path(current(plugin)["approved_snapshot"])
    path.write_text("# Changed working file", encoding="utf-8")
    if damage == "snapshot":
        snapshot.write_text("# Changed snapshot", encoding="utf-8")
    elif damage == "snapshot-symlink":
        backup = snapshot.rename(tmp_path / "saved.md")
        snapshot.symlink_to(backup)
    elif damage == "approved-dir-symlink":
        backup = snapshot.parent.rename(tmp_path / "saved")
        snapshot.parent.symlink_to(backup, target_is_directory=True)
    session_env["HERMES_SESSION_KEY"] = "new-session"
    if damage == "working-file":
        assert plugin.command("resume").startswith("Resuming launch.md rev 2")
        assert current(plugin)["approved_snapshot"] == str(snapshot)
        assert f"the approved text is {snapshot}" in plugin.pre_llm_call()["context"]
    else:
        assert "changed after it was approved" in plugin.command("resume")
        assert not current(plugin)


@pytest.mark.parametrize("name, expected", [
    ("launch-alpha.md", "Resuming launch-alpha.md"),
    ("launch-alpha", "Resuming launch-alpha.md"),
    ("launch-a", "Resuming launch-alpha.md"),
    ("launch", "ambiguous"),
    ("../x", "No approved plan to resume"),
    (r"launch\alpha", "No approved plan to resume"),
    ("absolute", "No approved plan to resume"),
    ("symlink", "No approved plan to resume"),
])
def test_resume_matches_names_inside_plans_dir(plugin, session_env, tmp_path, name, expected):
    alpha = approve(plugin, "launch-alpha.md")
    beta = approve(plugin, "launch-beta.md")
    if name == "absolute":
        name = str(alpha)
    if name == "symlink":
        outside = beta.rename(tmp_path / "outside.md")
        beta.symlink_to(outside)
        name = beta.name
    session_env["HERMES_SESSION_KEY"] = "new-session"
    response = plugin.command(f"resume {name}")
    assert expected in response
    if expected == "ambiguous":
        assert alpha.name in response and beta.name in response
    if not expected.startswith("Resuming"):
        assert not current(plugin)


def test_resume_default_uses_newest_existing_unfinished_plan(plugin, session_env):
    approve(plugin, "old.md")
    wanted = approve(plugin, "wanted.md")
    approve(plugin, "finished.md")
    plugin.command("done")
    approve(plugin, "gone.md").unlink()
    session_env["HERMES_SESSION_KEY"] = "new-session"
    assert plugin.command("resume").startswith(f"Resuming {wanted.name}")


@pytest.mark.parametrize("symlink_dir", [False, True])
def test_resume_never_crosses_plans_directories(plugin, session_env, tmp_path, symlink_dir):
    path = approve(plugin)
    other = tmp_path / "other"
    other.mkdir()
    if symlink_dir:
        (other / ".hermes").symlink_to(path.parent.parent, target_is_directory=True)
    session_env.update(TERMINAL_CWD=str(other), HERMES_SESSION_KEY="new-session")
    assert "No approved plan to resume" in plugin.command("resume")
    assert not current(plugin)


def test_resume_restores_review_each_edit(plugin, session_env, monkeypatch):
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    plugin.ctx.settings["levers"] = "approval_card"
    monkeypatch.setattr(pm, "_edit_review_available", lambda: True)
    path = write_plan(plugin)
    answer_card(plugin, submit(plugin, path), REVIEW)
    key = current(plugin)["edit_review"]["key"]
    assert record(plugin, path)["edit_review"] is True
    session_env["HERMES_SESSION_KEY"] = "new-session"
    assert plugin.command("resume").startswith("Resuming launch.md rev 1")
    review = current(plugin)["edit_review"]
    assert (review["path"], review["rev"]) == (str(path), 1)
    assert review["key"].startswith("plan-mode-edit:") and review["key"] != key


def test_resume_approval_survives_record_write_error(plugin, monkeypatch, caplog):
    original = plugin.ctx.state.set
    attempts = []

    def fail_record_write(key, value):
        if key == RECORDS:
            attempts.append(key)
            raise OSError("store unavailable")
        original(key, value)

    monkeypatch.setattr(plugin.ctx.state, "set", fail_record_write)
    with caplog.at_level("DEBUG"):
        path = approve(plugin)
    assert current(plugin)["approved_path"] == str(path)
    assert current(plugin)["phase"] == "executing"
    assert attempts == [RECORDS] and "store unavailable" in caplog.text


def test_resume_bounds_records_and_reinserts_updated_plan(plugin):
    first = approve(plugin, "plan-0.md", submitted=False)
    for index in range(1, 200):
        approve(plugin, f"plan-{index}.md", submitted=False)
    approve(plugin, first.name, submitted=False)
    last = approve(plugin, "plan-200.md", submitted=False)
    records = plugin.ctx.state.get(RECORDS, {})
    assert len(records) == 200
    assert str(first.with_name("plan-1.md")) not in records
    assert list(records)[-2:] == [str(first), str(last)]


def test_resume_keeps_no_commit_guard(plugin):
    plugin.ctx.settings["allow_commits"] = False
    approve(plugin, submitted=False)
    plugin.command("off")
    assert plugin.command("resume").startswith("Resuming launch.md")
    assert plugin.pre_tool_call("terminal", {"command": 'git commit -m "no"'})["action"] == "block"


@pytest.mark.parametrize("surface", ["tui", "desktop"])
def test_resume_unresolved_tab_reuses_done_refusal(plugin, session_env, surface):
    approve(plugin)
    session_env.update(HERMES_SESSION_PLATFORM=surface, HERMES_UI_SESSION_ID="owning-tab",
                       HERMES_SESSION_KEY="tab-key")
    plugin.command("on")
    before = deepcopy(plugin.ctx.state.values)
    session_env.pop("HERMES_UI_SESSION_ID")
    session_env["HERMES_SESSION_KEY"] = "unresolved-key"
    assert plugin.command("resume") == plugin.command("done")
    assert "did not bind the stable UI session ID" in plugin.command("resume")
    assert plugin.ctx.state.values == before
    session_env.update(HERMES_SESSION_KEY="tab-key", HERMES_UI_SESSION_ID="owning-tab")
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"
    plugin.command("off")
    assert plugin.command("resume").startswith("Resuming launch.md")
    session_env.pop("HERMES_UI_SESSION_ID")
    assert "Phase: executing" in plugin.command("status")


def test_resume_first_cli_input_before_session_id(plugin, session_env):
    path = approve(plugin)
    session_env.pop("HERMES_SESSION_KEY")
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    assert plugin.command("resume").startswith("Resuming launch.md rev 1")
    assert plugin._load_state(f"cli:{pm.os.getpid()}")["approved_path"] == str(path)
    assert "Resuming the approved plan" in plugin.pre_llm_call(session_id="published-now")["context"]
    assert current(plugin)["session_id_hash"] == plugin._session_id_hash("published-now")


@pytest.mark.parametrize("surface", ["cli", "tui"])
def test_resume_survives_session_cleanup(plugin, session_env, surface):
    session_env["HERMES_SESSION_PLATFORM"] = surface
    if surface == "cli":
        session_env.pop("HERMES_SESSION_KEY")
    else:
        session_env["HERMES_UI_SESSION_ID"] = "tui-session"
    path = approve(plugin, submitted=False)
    if surface == "cli":
        plugin.on_session_finalize(platform=surface)
    else:
        pm.PlanModePlugin(plugin.ctx).on_session_reset(platform=surface)
    assert not current(plugin)
    assert record(plugin, path)["status"] == "approved"
    assert plugin.command("resume").startswith("Resuming launch.md")


def test_later_progress_cannot_reopen_finished_approval(plugin, session_env):
    path = approve(plugin)
    first = pm.PlanModePlugin(plugin.ctx)
    second = pm.PlanModePlugin(plugin.ctx)
    session_env["HERMES_SESSION_KEY"] = "first"
    assert first.command("resume").startswith("Resuming")
    session_env["HERMES_SESSION_KEY"] = "second"
    assert second.command("resume").startswith("Resuming")
    session_env["HERMES_SESSION_KEY"] = "first"
    first.command("done")
    finished = deepcopy(record(plugin, path))
    session_env["HERMES_SESSION_KEY"] = "second"
    todo(second, "completed", "in_progress")
    assert finished["status"] == "finished"
    assert record(plugin, path) == finished


def test_resume_respects_session_profile(plugin, session_env):
    approve(plugin)
    session_env.update(HERMES_SESSION_KEY="new-session", HERMES_SESSION_PROFILE="other")
    plugin._registration_profile = "home"
    before = deepcopy(plugin.ctx.state.values)
    assert "session belongs to profile 'other'" in plugin.command("resume")
    assert plugin.ctx.state.values == before


def test_correlated_directive_approval_is_resumable(plugin, session_env):
    path = write_plan(plugin)
    directive = _submit(plugin)
    _decision(plugin, directive)
    assert _submit_result(plugin)["approved"]
    assert record(plugin, path)["revision"] == 1
    session_env["HERMES_SESSION_KEY"] = "new-session"
    assert plugin.command("resume").startswith("Resuming launch.md rev 1")
