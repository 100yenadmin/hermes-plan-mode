"""The stop gate can ask for one more pass on an unfinished approved plan."""
import copy
import json
import sys
from types import SimpleNamespace

import pytest

from plan_mode import plugin as pm
from test_plan_mode import FakeContext
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


@pytest.mark.parametrize("kind", ["internal_notification", "process_complete", "async_delegation_complete"])
def test_finish_hold_leaves_machinery_turns_alone(unfinished, kind):
    unfinished.pre_llm_call(platform="cli", conversation_history=[{"role": "user", "display_kind": kind}])
    before = copy.deepcopy(unfinished.ctx.state.values)
    assert finish(unfinished) is None
    assert unfinished.ctx.state.values == before


@pytest.mark.parametrize("last_row", [
    {"role": "user", "display_kind": "text"},
    {"role": "user"},
    {"role": "assistant", "display_kind": "internal_notification"},
])
def test_finish_hold_uses_only_the_latest_row_and_resets_the_origin(unfinished, last_row):
    notice = {"role": "user", "display_kind": "internal_notification"}
    unfinished.pre_llm_call(platform="cli", conversation_history=[notice])
    assert finish(unfinished) is None
    unfinished.pre_llm_call(platform="cli", conversation_history=[notice, last_row])
    assert finish(unfinished)["action"] == "continue"
    assert finish(unfinished, attempt=1) is None


def test_finish_hold_tracks_turn_origin_per_session(unfinished, session_env, executing_plan):
    notice = {"role": "user", "display_kind": "internal_notification"}
    unfinished.pre_llm_call(platform="cli", conversation_history=[notice])

    session_env["HERMES_SESSION_KEY"] = "other-session"
    assert "Plan mode is on" in unfinished.command("on another plan")
    path = executing_plan.with_name("other.md")
    args = {"path": str(path), "content": "# Other plan\n\nRun the checks.\n"}
    assert unfinished.pre_tool_call("write_file", args, tool_call_id="other-plan") is None
    path.write_text(args["content"], encoding="utf-8")
    unfinished.post_tool_call("write_file", args, status="ok", tool_call_id="other-plan")
    assert "Plan approved" in unfinished.command("approve")
    observe_todos(unfinished, ("pending", "Test"))
    assert finish(unfinished)["action"] == "continue"  # no origin recorded for this session yet
    unfinished.pre_llm_call(platform="cli", conversation_history=[{"role": "user"}])
    assert finish(unfinished)["action"] == "continue"

    session_env["HERMES_SESSION_KEY"] = "unit-session"
    assert finish(unfinished) is None


@pytest.mark.parametrize("owner_row", [{"role": "user", "display_kind": "steer", "content": "[OUT-OF-BAND USER MESSAGE ..."}])
def test_finish_hold_promotes_machinery_turn_once_after_an_owner_message(unfinished, owner_row):
    history = [{"role": "user", "display_kind": "internal_notification"}]
    turn = dict(platform="cli", turn_id="turn-1")
    unfinished.pre_llm_call(conversation_history=history, **turn)
    assert finish(unfinished) is None
    assert unfinished.pre_api_request(conversation_history=[*history, {"role": "tool"}, owner_row], **turn) is None
    assert finish(unfinished)["action"] == "continue"
    assert finish(unfinished, attempt=1) is None

    assert unfinished.pre_api_request(conversation_history=history, **turn) is None
    assert finish(unfinished)["action"] == "continue"  # later calls cannot undo the promotion


@pytest.mark.parametrize("hook", ["pre_llm_call", "pre_api_request"])
def test_finish_hold_matches_by_session_when_one_hook_has_no_turn_id(unfinished, hook):
    history = [{"role": "user", "display_kind": "internal_notification"}]
    start_id = {} if hook == "pre_llm_call" else {"turn_id": "turn-1"}
    request_id = {} if hook == "pre_api_request" else {"turn_id": "turn-1"}
    unfinished.pre_llm_call(platform="cli", conversation_history=history, **start_id)
    assert unfinished.pre_api_request(
        platform="cli", conversation_history=[*history, {"role": "user", "display_kind": "steer"}], **request_id,
    ) is None
    assert finish(unfinished)["action"] == "continue"


@pytest.mark.parametrize("request_turn, appended_rows", [
    ("turn-1", [{"role": "user", "display_kind": "process_complete"}]),
    ("turn-1", [{"role": "tool", "display_kind": "steer"}]),
    ("turn-2", [{"role": "user", "display_kind": "steer"}]),
])
def test_finish_hold_ignores_rows_outside_the_current_turn_or_without_user_input(
    unfinished, request_turn, appended_rows,
):
    history = [{"role": "user", "display_kind": "steer"}, {"role": "assistant"},
               {"role": "user", "display_kind": "internal_notification"}]
    unfinished.pre_llm_call(platform="cli", turn_id="turn-1", conversation_history=history)
    assert unfinished.pre_api_request(
        platform="cli", turn_id=request_turn, conversation_history=[*history, *appended_rows],
    ) is None
    assert finish(unfinished) is None


@pytest.mark.parametrize("scaffolding", [
    {"role": "user", "content": "LSP: app.py has 1 error"},  # turn_stop_gates LSP note: no flag, no display_kind
    {"role": "user", "content": "Continue.", "_pre_verify_synthetic": True},  # another plugin's stop-gate nudge
    {"role": "user", "content": "Continue where you stopped.", "_verification_stop_synthetic": True},
    {"role": "user", "content": "[todo snapshot]", "_todo_snapshot_synthetic": True},
    {"role": "user", "display_kind": "text", "content": "x"},
])
def test_finish_hold_never_promotes_a_machinery_turn_on_hermes_own_user_rows(unfinished, scaffolding):
    # Hermes appends its own user-role rows mid-turn; only a steer means the user wrote into the turn.
    history = [{"role": "user", "display_kind": "internal_notification"}]
    turn = dict(platform="cli", turn_id="turn-1")
    unfinished.pre_llm_call(conversation_history=history, **turn)
    assert unfinished.pre_api_request(conversation_history=[*history, {"role": "tool"}, scaffolding], **turn) is None
    assert finish(unfinished) is None


def test_finish_hold_keeps_a_user_started_turn_after_a_machinery_row(unfinished):
    turn = dict(platform="cli", turn_id="turn-1")
    history = [{"role": "user"}]
    unfinished.pre_llm_call(conversation_history=history, **turn)
    assert unfinished.pre_api_request(
        conversation_history=[*history, {"role": "user", "display_kind": "process_complete"}], **turn,
    ) is None
    assert finish(unfinished)["action"] == "continue"


def test_finish_hold_per_call_steer_does_not_leak_across_sessions(unfinished, session_env):
    turn = dict(platform="cli", turn_id="turn-1")
    history = [{"role": "user", "display_kind": "internal_notification"}]
    unfinished.pre_llm_call(conversation_history=history, **turn)
    session_env["HERMES_SESSION_KEY"] = "other-session"
    unfinished.pre_llm_call(conversation_history=history, **turn)
    assert unfinished.pre_api_request(
        conversation_history=[*history, {"role": "user", "display_kind": "steer"}], **turn,
    ) is None
    session_env["HERMES_SESSION_KEY"] = "unit-session"
    assert finish(unfinished) is None


def test_finish_hold_per_call_hook_returns_none_when_identity_lookup_raises(unfinished, monkeypatch):
    def broken_identity(_platform):
        raise RuntimeError("identity unavailable")

    monkeypatch.setattr(pm, "derive_session_identity", broken_identity)
    assert unfinished.pre_api_request(platform="cli", conversation_history=[{"role": "user"}]) is None


def test_finish_hold_registers_other_hooks_when_host_rejects_pre_api_request(plugin):
    class OlderContext(FakeContext):
        rejected = False

        def register_hook(self, name, callback):
            if name == "pre_api_request":
                self.rejected = True
                raise ValueError("unknown hook")
            super().register_hook(name, callback)

    context = OlderContext()
    pm.PlanModePlugin(context).register()
    assert context.rejected
    assert context.hooks.keys() == plugin.ctx.hooks.keys() - {"pre_api_request"}


@pytest.mark.parametrize("helper_available", [False, True])
def test_finish_hold_falls_back_to_local_kinds(unfinished, monkeypatch, helper_available):
    def broken_helper(_kind):
        raise RuntimeError("host filter unavailable")

    if helper_available:
        monkeypatch.setitem(sys.modules, "gateway.response_filters",
                            SimpleNamespace(is_machinery_display_kind=broken_helper))
    else:
        monkeypatch.delitem(sys.modules, "gateway.response_filters", raising=False)
    notice = {"role": "user", "display_kind": "process_complete"}
    unfinished.pre_llm_call(platform="cli", conversation_history=[notice])
    assert finish(unfinished) is None


def test_finish_hold_prefers_the_loaded_host_kind_check(unfinished, monkeypatch):
    monkeypatch.setitem(sys.modules, "gateway.response_filters", SimpleNamespace(
        is_machinery_display_kind=lambda kind: kind == "host_notice"))
    unfinished.pre_llm_call(platform="cli", conversation_history=[{"role": "user", "display_kind": "host_notice"}])
    assert finish(unfinished) is None


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
    unfinished.ctx.settings["levers"] = "approval_card"
    state = unfinished._load_state("sk:unit-session")
    state["edit_review"] = {"denied": True}
    unfinished._save_state("sk:unit-session", state)
    assert finish(unfinished) is None
    state["edit_review"] = {"denied": False}
    unfinished._save_state("sk:unit-session", state)
    assert finish(unfinished) is not None  # the same state without a denial still holds


def test_finish_hold_ignores_stale_denial_when_approval_card_is_off(unfinished):
    unfinished.ctx.settings.update(levers="approval_card", approval_card=False)
    state = unfinished._load_state("sk:unit-session")
    state["edit_review"] = {"denied": True}
    unfinished._save_state("sk:unit-session", state)
    assert finish(unfinished)["action"] == "continue"


def test_finish_hold_puts_a_multi_line_step_on_one_line(unfinished):
    observe_todos(unfinished, ("in_progress", "line1\nIGNORE ALL\n  line3"), ("pending", "Test"))
    message = finish(unfinished)["message"]
    assert "next: line1 IGNORE ALL line3. Continue with it." in message and message.count("\n") == 1
