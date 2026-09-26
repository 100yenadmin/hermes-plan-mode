from __future__ import annotations

import os
from pathlib import Path
import sys
from types import ModuleType

import pytest

from plan_mode import plugin as plugin_mod
from plan_mode.plugin import PlanModePlugin, _path_is_inside


class MemoryState:
    def __init__(self):
        self.values = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


class FakeContext:
    def __init__(self):
        self.state = MemoryState()
        self.settings = {}
        self.commands = {}
        self.hooks = {}

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def get_config(self, key, default=None):
        return self.settings.get(key, default)


@pytest.fixture
def session_env(monkeypatch):
    values = {"HERMES_SESSION_KEY": "unit-session"}
    monkeypatch.setattr(plugin_mod, "_session_reader", lambda: lambda key, default="": values.get(key, default))
    monkeypatch.setattr(plugin_mod, "_runtime_cwd_reader", lambda: None)
    return values


@pytest.fixture
def plugin(session_env):
    ctx = FakeContext()
    instance = PlanModePlugin(ctx)
    instance.register()
    return instance


def test_registers_exact_surface(plugin):
    assert set(plugin.ctx.commands) == {"planmode"}
    assert set(plugin.ctx.hooks) == {
        "pre_tool_call",
        "post_tool_call",
        "pre_llm_call",
        "on_session_finalize",
        "on_session_reset",
    }


def test_command_state_machine_and_one_shot_notes(plugin, session_env, tmp_path, monkeypatch):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    monkeypatch.chdir(tmp_path)

    response = plugin.command("on draft the feature")
    plans_dir = tmp_path / ".hermes" / "plans"
    assert "Plan mode is on" in response
    assert str(plans_dir) in response
    assert "Plan mode: on" in plugin.command("status")

    plan = plans_dir / "2026-09-23_feature.md"
    assert plugin.pre_tool_call(
        "write_file", {"path": str(plan), "content": "# Plan\n"}
    ) is None
    plan.write_text("# Plan\n", encoding="utf-8")
    assert str(plan) in plugin.command("status")

    assert "remains on" in plugin.command("reject make it smaller")
    first = plugin.pre_llm_call()
    assert "The user rejected the plan: make it smaller. Revise it." in first["context"]
    second = plugin.pre_llm_call()
    assert "The user rejected" not in second["context"]

    assert "Plan approved" in plugin.command("approve")
    approve_note = plugin.pre_llm_call()
    assert approve_note == {"context": f"The user approved the plan at {plan}. Implement it now."}
    assert plugin.pre_llm_call() is None
    assert "not on" in plugin.command("approve")

    plugin.command("on again")
    assert "No approval note" in plugin.command("off")
    assert plugin.pre_llm_call() is None


def test_approve_uses_only_this_sessions_tracked_plan_files(
    plugin, session_env, tmp_path
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plans = tmp_path / ".hermes" / "plans"

    session_env["HERMES_SESSION_KEY"] = "session-a"
    plugin.command("on")
    plan_a = plans / "2026-09-23_a.md"
    assert plugin.pre_tool_call("write_file", {"path": str(plan_a), "content": "A"}) is None
    plan_a.write_text("A", encoding="utf-8")

    session_env["HERMES_SESSION_KEY"] = "session-b"
    plugin.command("on")
    plan_b = plans / "2026-09-23_z.md"
    assert plugin.pre_tool_call("write_file", {"path": str(plan_b), "content": "B"}) is None
    plan_b.write_text("B", encoding="utf-8")

    session_env["HERMES_SESSION_KEY"] = "session-a"
    response = plugin.command("approve")
    assert str(plan_a) in response
    assert str(plan_b) not in response

    plugin.command("on")
    newer_a = plans / "2026-09-23_newer-a.md"
    assert plugin.pre_tool_call("write_file", {"path": str(newer_a), "content": "A2"}) is None
    newer_a.write_text("A2", encoding="utf-8")
    explicit = plugin.command(f"approve {plan_a.name}")
    assert str(plan_a) in explicit
    assert str(newer_a) not in explicit


@pytest.mark.parametrize("status", ["error", "blocked", "cancelled"])
def test_failed_plan_write_never_becomes_approvable(plugin, session_env, tmp_path, status):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plans = tmp_path / ".hermes" / "plans"
    good, failed = plans / "2026-09-26_a-good.md", plans / "2026-09-26_b-failed.md"
    for path, call_id, outcome in ((good, "call-good", "ok"), (failed, "call-bad", status)):
        args = {"path": str(path), "content": "# Plan\n"}
        ids = {"session_id": "s1", "tool_call_id": call_id}
        assert plugin.pre_tool_call("write_file", args, **ids) is None
        path.write_text("# Plan\n", encoding="utf-8")  # the target exists either way
        plugin.post_tool_call("write_file", args, status=outcome, **ids)

    assert str(failed) not in plugin.command("status")
    assert "not written by this session" in plugin.command(f"approve {failed.name}")
    assert str(good) in plugin.command("approve")


def test_plan_write_needs_ok_post_for_the_same_call(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plan = tmp_path / ".hermes" / "plans" / "2026-09-26_plan.md"
    args = {"path": str(plan), "content": "# Plan\n"}
    assert plugin.pre_tool_call("write_file", args, session_id="s1", tool_call_id="c1") is None
    plan.write_text("# Plan\n", encoding="utf-8")
    plugin.post_tool_call("write_file", args, session_id="s1", tool_call_id="c2", status="ok")
    plugin.post_tool_call("write_file", args, session_id="s2", tool_call_id="c1", status="ok")
    other = {"path": str(plan.with_name("other.md")), "content": "x"}
    plugin.post_tool_call("write_file", other, session_id="s1", tool_call_id="c1", status="ok")

    assert "no tracked plan file" in plugin.command("approve")
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"


def test_approve_refuses_without_a_tracked_plan_file(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    response = plugin.command("approve")
    assert "Plan approval was refused: this session has no tracked plan file" in response
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"


def test_on_falls_back_to_process_cwd_for_invalid_terminal_cwd(plugin, session_env, tmp_path, monkeypatch):
    session_env["TERMINAL_CWD"] = "relative/missing"
    monkeypatch.chdir(tmp_path)
    response = plugin.command("on")
    assert str(tmp_path / ".hermes" / "plans") in response


def test_on_refuses_bound_profile_when_registration_profile_is_unknown(
    session_env, tmp_path
):
    session_env.update(
        {
            "HERMES_SESSION_PROFILE": "profile-a",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    ctx = FakeContext()
    plugin = PlanModePlugin(ctx)

    response = plugin.command("on")

    assert "refused" in response.lower()
    assert "registration profile" in response.lower()
    assert ctx.state.values == {}


@pytest.mark.parametrize("action", ["off", "approve", "reject revise it"])
def test_mutating_commands_refuse_cross_profile_session(
    plugin, session_env, tmp_path, action
):
    plugin._registration_profile = "profile-a"
    session_env.update(
        {"HERMES_SESSION_PROFILE": "profile-a", "TERMINAL_CWD": str(tmp_path)}
    )
    assert "Plan mode is on" in plugin.command("on")
    before = {key: dict(value) if isinstance(value, dict) else value
              for key, value in plugin.ctx.state.values.items()}

    session_env["HERMES_SESSION_PROFILE"] = "profile-b"
    response = plugin.command(action)

    assert "refused" in response.lower()
    assert "profile-b" in response
    assert plugin.ctx.state.values == before
    session_env["HERMES_SESSION_PROFILE"] = "profile-a"
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"
    assert "Plan mode: on" in plugin.command("status")


@pytest.mark.parametrize("action", ["off", "approve", "reject revise it"])
def test_mutating_commands_refuse_when_registration_profile_is_unknown(
    plugin, session_env, tmp_path, action
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    assert "Plan mode is on" in plugin.command("on")
    before = {key: dict(value) if isinstance(value, dict) else value
              for key, value in plugin.ctx.state.values.items()}

    plugin._registration_profile = None
    session_env["HERMES_SESSION_PROFILE"] = "profile-a"
    response = plugin.command(action)

    assert "refused" in response.lower()
    assert "registration profile" in response.lower()
    assert plugin.ctx.state.values == before


def test_read_allowlist_and_unknown_blocks(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    assert plugin.pre_tool_call("read_file", {"path": "/tmp/x"}) is None
    assert plugin.pre_tool_call("browser_snapshot", {}) is None
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"
    assert plugin.pre_tool_call("mcp_linear_update_issue", {})["action"] == "block"
    assert plugin.pre_tool_call("totally_new_tool", {})["action"] == "block"


def _fake_skill_loader(monkeypatch, loader):
    module = ModuleType("agent.skill_preprocessing")
    if loader is not None:
        module.load_skills_config = loader
    monkeypatch.setitem(sys.modules, "agent.skill_preprocessing", module)


def test_skill_view_is_blocked_when_inline_shell_is_enabled(
    plugin, session_env, tmp_path, monkeypatch
):
    skills_cfg = {"inline_shell": True}
    _fake_skill_loader(monkeypatch, lambda: skills_cfg)
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")

    blocked = plugin.pre_tool_call("skill_view", {"name": "unsafe-skill"})
    assert blocked["action"] == "block"
    assert "skill_view is blocked" in blocked["message"]
    assert "inline_shell" in blocked["message"]

    skills_cfg["inline_shell"] = False
    assert plugin.pre_tool_call("skill_view", {"name": "safe-skill"}) is None


def test_skill_view_is_allowed_when_hermes_reports_inline_shell_off(
    plugin, session_env, tmp_path, monkeypatch
):
    _fake_skill_loader(monkeypatch, lambda: {})
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")

    assert plugin.pre_tool_call("skill_view", {"name": "any-skill"}) is None
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"
    assert plugin.pre_tool_call(
        "write_file", {"path": str(tmp_path / "outside.md"), "content": "x"}
    )["action"] == "block"


def _raise_loader():
    raise RuntimeError("config unreadable")


@pytest.mark.parametrize(
    "loader",
    [None, _raise_loader, lambda: None, lambda: ["inline_shell", False]],
    ids=["loader-missing", "loader-raises", "returns-none", "returns-non-dict"],
)
def test_skill_view_fails_closed_when_hermes_skill_loader_is_unusable(
    plugin, session_env, tmp_path, monkeypatch, loader
):
    _fake_skill_loader(monkeypatch, loader)
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")

    blocked = plugin.pre_tool_call("skill_view", {"name": "any-skill"})

    assert blocked["action"] == "block"
    assert "skill_view is blocked" in blocked["message"]


def test_skill_view_fails_closed_when_skill_module_is_missing(
    plugin, session_env, tmp_path, monkeypatch
):
    monkeypatch.setitem(sys.modules, "agent.skill_preprocessing", None)
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")

    assert plugin.pre_tool_call("skill_view", {"name": "any-skill"})["action"] == "block"


def test_extra_allowed_tools_extend_allowlist(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.ctx.settings["plan_mode.extra_allowed_tools"] = ["custom_read"]
    plugin.command("on")
    assert plugin.pre_tool_call("custom_read", {}) is None


def test_write_file_requires_absolute_contained_path(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plans = tmp_path / ".hermes" / "plans"
    inside = plans / "plan.md"
    outside = tmp_path / "implementation.py"

    assert plugin.pre_tool_call("write_file", {"path": str(inside), "content": "x"}) is None
    assert plugin.pre_tool_call("write_file", {"path": "plan.md", "content": "x"})["action"] == "block"
    assert plugin.pre_tool_call("write_file", {"path": str(outside), "content": "x"})["action"] == "block"
    traversal = plans / ".." / ".." / "escape.md"
    assert plugin.pre_tool_call("write_file", {"path": str(traversal), "content": "x"})["action"] == "block"
    inside_traversal = plans / "drafts" / ".." / "plan.md"
    assert plugin.pre_tool_call("write_file", {"path": str(inside_traversal), "content": "x"})["action"] == "block"


def test_symlink_escape_is_blocked(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plans = tmp_path / ".hermes" / "plans"
    outside = tmp_path / "outside"
    outside.mkdir()
    link = plans / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    escaped = link / "plan.md"
    assert not _path_is_inside(str(escaped), str(plans))
    assert plugin.pre_tool_call("write_file", {"path": str(escaped), "content": "x"})["action"] == "block"


def test_plan_root_symlink_swap_after_activation_is_blocked(
    plugin, session_env, tmp_path
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plans = tmp_path / ".hermes" / "plans"
    original = tmp_path / ".hermes" / "plans-original"
    outside = tmp_path / "outside-after-activation"
    outside.mkdir()
    plans.rename(original)
    try:
        plans.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    escaped = plans / "plan.md"
    blocked = plugin.pre_tool_call(
        "write_file", {"path": str(escaped), "content": "x"}
    )

    assert blocked["action"] == "block"
    assert "plans directory" in blocked["message"]


@pytest.mark.parametrize("symlink_component", [".hermes", ".hermes/plans"])
def test_activation_refuses_symlinked_plan_root_component(
    plugin, session_env, tmp_path, symlink_component
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    outside = tmp_path / "outside-root"
    outside.mkdir()
    link = tmp_path / symlink_component
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    response = plugin.command("on")

    assert "refused" in response.lower()
    assert "symlink" in response.lower()
    assert "Plan mode: off" in plugin.command("status")


def test_patch_checks_every_target(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plans = tmp_path / ".hermes" / "plans"
    safe_a = plans / "a.md"
    safe_b = plans / "b.md"
    unsafe = tmp_path / "outside.md"

    safe_patch = (
        f"*** Begin Patch\n*** Add File: {safe_a}\n+x\n"
        f"*** Add File: {safe_b}\n+y\n*** End Patch"
    )
    mixed_patch = (
        f"*** Begin Patch\n*** Add File: {safe_a}\n+x\n"
        f"*** Add File: {unsafe}\n+y\n*** End Patch"
    )
    assert plugin.pre_tool_call("patch", {"mode": "patch", "patch": safe_patch}) is None
    assert plugin.pre_tool_call("patch", {"mode": "patch", "patch": mixed_patch})["action"] == "block"
    assert plugin.pre_tool_call("patch", {"mode": "replace", "path": str(safe_a)}) is None
    assert plugin.pre_tool_call("patch", {"mode": "patch", "patch": "no headers"})["action"] == "block"


def test_pre_tool_callback_fails_closed_on_internal_exception(plugin, session_env, tmp_path, monkeypatch):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    monkeypatch.setattr(plugin, "_load_state", lambda key: (_ for _ in ()).throw(RuntimeError("boom")))
    result = plugin.pre_tool_call("read_file", {"path": "/tmp/x"})
    assert result["action"] == "block"
    assert "failed closed" in result["message"]


def test_pre_llm_callback_fails_closed_when_active_index_read_raises(
    plugin, monkeypatch
):
    monkeypatch.setattr(
        plugin,
        "_active_storage_keys",
        lambda: (_ for _ in ()).throw(RuntimeError("state unavailable")),
    )

    result = plugin.pre_llm_call()

    assert "could not be read safely" in result["context"]


def test_missing_non_cli_key_overblocks_when_any_session_active(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    session_env["HERMES_SESSION_KEY"] = ""
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"
    result = plugin.pre_tool_call("read_file", {"path": "/tmp/x"})
    assert result["action"] == "block"
    assert "could not be derived" in result["message"]


def test_unbound_request_uses_durable_active_index_after_plugin_reload(
    plugin, session_env, tmp_path
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    assert "Plan mode is on" in plugin.command("on durable index")
    reloaded = PlanModePlugin(plugin.ctx)
    session_env["HERMES_SESSION_KEY"] = ""
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"

    blocked = reloaded.pre_tool_call("terminal", {"command": "pwd"})
    context = reloaded.pre_llm_call()

    assert blocked["action"] == "block"
    assert "could not be derived" in blocked["message"]
    assert "session key could not be derived" in context["context"]


def test_unbound_cron_request_ignores_active_state_owned_by_another_process(
    plugin, session_env, monkeypatch
):
    plugin._save_state(
        "sk:other-process-session",
        {
            "active": True,
            "owner_pid": os.getpid() + 10_000,
            "plans_dir": "/other-process/.hermes/plans",
        },
    )
    session_env.clear()
    monkeypatch.setattr(plugin_mod, "_session_context_is_engaged", lambda: True)

    assert plugin.pre_tool_call("terminal", {"command": "pwd"}) is None
    assert plugin.pre_llm_call() is None


def test_cron_marked_unbound_request_ignores_same_process_plan_mode(
    plugin, session_env, tmp_path, monkeypatch
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    assert "Plan mode is on" in plugin.command("on")
    session_env.clear()
    session_env["HERMES_CRON_SESSION"] = "1"
    monkeypatch.setattr(plugin_mod, "_session_context_is_engaged", lambda: True)

    assert plugin.pre_tool_call("terminal", {"command": "pwd"}) is None
    assert plugin.pre_llm_call() is None

    session_env.pop("HERMES_CRON_SESSION")
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"

    session_env.update(
        {"HERMES_SESSION_KEY": "unit-session", "HERMES_CRON_SESSION": "1"}
    )
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"


def test_missing_internal_import_refuses_on_and_hooks_do_not_block(monkeypatch):
    ctx = FakeContext()
    plugin = PlanModePlugin(ctx)
    monkeypatch.setattr(plugin_mod, "_session_reader", lambda: None)
    assert "unavailable" in plugin.command("on")
    assert plugin.pre_tool_call("terminal", {}) is None


def test_cli_process_key_survives_session_id_rotation(monkeypatch):
    values = {"HERMES_SESSION_ID": "before"}
    monkeypatch.setattr(plugin_mod, "_session_reader", lambda: lambda key, default="": values.get(key, default))
    before = plugin_mod.derive_session_identity().key
    values["HERMES_SESSION_ID"] = "after"
    after = plugin_mod.derive_session_identity().key
    assert before == after == f"cli:{os.getpid()}"


@pytest.mark.parametrize("surface", ["", "cli"])
def test_nested_cli_ignores_inherited_parent_session_key(monkeypatch, surface):
    values = {
        "HERMES_SESSION_KEY": "parent-session-key",
        "HERMES_SESSION_SOURCE": surface,
    }
    monkeypatch.setenv("HERMES_SESSION_KEY", "parent-session-key")
    monkeypatch.setattr(
        plugin_mod,
        "_session_reader",
        lambda: lambda key, default="": values.get(key, default),
    )

    identity = plugin_mod.derive_session_identity()

    assert identity.key == f"cli:{os.getpid()}"


def test_nested_cli_ignores_all_inherited_parent_session_identity(monkeypatch):
    values = {
        "HERMES_SESSION_KEY": "parent-session-key",
        "HERMES_SESSION_SOURCE": "tui",
        "HERMES_SESSION_PLATFORM": "desktop",
        "HERMES_UI_SESSION_ID": "parent-ui-tab",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        plugin_mod,
        "_session_reader",
        lambda: lambda key, default="": values.get(key, default),
    )
    monkeypatch.setattr(plugin_mod, "_session_context_is_engaged", lambda: False)
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")

    identity = plugin_mod.derive_session_identity()

    assert identity.key == f"cli:{os.getpid()}"


def test_inherited_slash_worker_identity_refuses_activation(
    monkeypatch, tmp_path
):
    values = {
        "HERMES_SESSION_KEY": "parent-session-key",
        "HERMES_SESSION_SOURCE": "tui",
        "HERMES_SESSION_PLATFORM": "desktop",
        "HERMES_UI_SESSION_ID": "parent-ui-tab",
        "TERMINAL_CWD": str(tmp_path),
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.setattr(
        plugin_mod,
        "_session_reader",
        lambda: lambda key, default="": values.get(key, default),
    )
    monkeypatch.setattr(plugin_mod, "_session_context_is_engaged", lambda: False)
    plugin = PlanModePlugin(FakeContext())

    response = plugin.command("on slash worker")

    assert "refused" in response.lower()
    assert "gateway" in response.lower()


def test_unbound_server_surface_refuses_activation(session_env, plugin, monkeypatch):
    session_env.clear()
    monkeypatch.setattr(plugin_mod, "_session_context_is_engaged", lambda: True)

    response = plugin.command("on")

    assert "refused" in response.lower()
    assert "session binding" in response.lower()

    status = plugin.command("status")
    assert "Plan mode is unavailable on this surface:" in status
    assert "activation was refused" not in status.lower()


def test_legacy_messaging_gateway_first_command_refuses_cli_fallback(
    session_env, plugin, monkeypatch, tmp_path
):
    session_env.clear()
    session_env["TERMINAL_CWD"] = str(tmp_path)
    monkeypatch.setattr(plugin_mod, "_session_context_is_engaged", lambda: False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.setenv("HERMES_EXEC_ASK", "1")
    gateway_run = ModuleType("gateway.run")
    gateway_run._gateway_runner_ref = lambda: object()
    monkeypatch.setitem(sys.modules, "gateway.run", gateway_run)

    response = plugin.command("on legacy gateway")

    assert "refused" in response.lower()
    assert "gateway" in response.lower()


def test_nested_cli_ignores_inherited_exec_ask_without_live_gateway(
    session_env, plugin, monkeypatch, tmp_path
):
    session_env.clear()
    session_env.update(
        {"HERMES_SESSION_SOURCE": "cli", "TERMINAL_CWD": str(tmp_path)}
    )
    monkeypatch.setattr(plugin_mod, "_session_context_is_engaged", lambda: False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.setenv("HERMES_EXEC_ASK", "1")
    gateway_run = ModuleType("gateway.run")
    gateway_run._gateway_runner_ref = lambda: None
    monkeypatch.setitem(sys.modules, "gateway.run", gateway_run)

    response = plugin.command("on nested cli")

    assert "Plan mode is on" in response


def test_cli_still_works_after_gateway_run_is_imported(
    session_env, plugin, monkeypatch, tmp_path
):
    session_env.clear()
    session_env["TERMINAL_CWD"] = str(tmp_path)
    monkeypatch.setattr(plugin_mod, "_session_context_is_engaged", lambda: False)
    monkeypatch.setitem(sys.modules, "gateway.run", ModuleType("gateway.run"))

    response = plugin.command("on imported gateway module")

    assert "Plan mode is on" in response
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"


def test_legacy_cli_state_blocks_when_turn_derives_session_key(
    session_env, plugin, tmp_path
):
    session_env.clear()
    session_env.update(
        {
            "HERMES_SESSION_SOURCE": "cli",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    assert "Plan mode is on" in plugin.command("on")

    session_env.update(
        {
            "HERMES_SESSION_KEY": "tui-turn-key",
            "HERMES_SESSION_SOURCE": "tui",
        }
    )

    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"
    assert "Plan mode is ON" in plugin.pre_llm_call()["context"]


def test_ui_adoption_never_deletes_active_current_process_cli_state(
    session_env, plugin, tmp_path
):
    session_env.clear()
    session_env.update(
        {"HERMES_SESSION_SOURCE": "cli", "TERMINAL_CWD": str(tmp_path)}
    )
    assert "Plan mode is on" in plugin.command("on legacy cli")
    cli_key = f"cli:{os.getpid()}"

    session_env.update(
        {
            "HERMES_SESSION_KEY": "bound-turn-key",
            "HERMES_SESSION_SOURCE": "tui",
            "HERMES_UI_SESSION_ID": "desktop-tab-9",
        }
    )
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"

    assert plugin._load_state(cli_key).get("active") is True
    plan = tmp_path / ".hermes" / "plans" / "2026-09-26_adopted.md"
    assert plugin.pre_tool_call("write_file", {"path": str(plan), "content": "x"}) is None
    plan.write_text("x", encoding="utf-8")

    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "Plan approved" in plugin.command("approve")
    session_env["HERMES_UI_SESSION_ID"] = "desktop-tab-9"
    assert "The user approved the plan" in plugin.pre_llm_call()["context"]
    assert plugin.pre_tool_call("terminal", {}) is None
    assert plugin._load_state(cli_key).get("active") is False

    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "Plan mode is on" in plugin.command("on legacy cli again")
    session_env["HERMES_UI_SESSION_ID"] = "desktop-tab-9"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"
    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "No approval note" in plugin.command("off")
    session_env["HERMES_UI_SESSION_ID"] = "desktop-tab-9"
    assert plugin.pre_tool_call("terminal", {}) is None


def test_durable_legacy_cli_state_blocks_after_plugin_reload(
    session_env, plugin, tmp_path
):
    session_env.clear()
    session_env.update(
        {"HERMES_SESSION_SOURCE": "cli", "TERMINAL_CWD": str(tmp_path)}
    )
    plugin.command("on")

    reloaded = PlanModePlugin(plugin.ctx)
    session_env.clear()
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"

    assert reloaded.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"
    assert "Plan mode is active" in reloaded.pre_llm_call()["context"]


def test_tui_state_survives_session_key_rotation_via_stable_ui_id(
    session_env, plugin, tmp_path
):
    session_env.update(
        {
            "HERMES_SESSION_KEY": "before-compression",
            "HERMES_SESSION_SOURCE": "tui",
            "HERMES_UI_SESSION_ID": "desktop-tab-7",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    # Plugin command paths on both target versions omit HERMES_UI_SESSION_ID.
    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "Plan mode is on" in plugin.command("on")

    session_env["HERMES_UI_SESSION_ID"] = "desktop-tab-7"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"

    session_env["HERMES_SESSION_KEY"] = "after-compression"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"
    assert "Plan mode is ON" in plugin.pre_llm_call()["context"]


def test_tui_commands_reach_ui_state_after_adoption_and_key_rotation(
    session_env, plugin, tmp_path
):
    session_env.update(
        {
            "HERMES_SESSION_KEY": "command-key-before",
            "HERMES_SESSION_SOURCE": "tui",
            "HERMES_UI_SESSION_ID": "",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    assert "Plan mode is on" in plugin.command("on command continuity")

    session_env["HERMES_UI_SESSION_ID"] = "desktop-tab-8"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"

    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "Plan mode: on" in plugin.command("status")
    assert "remains on" in plugin.command("reject revise the plan")
    session_env["HERMES_UI_SESSION_ID"] = "desktop-tab-8"
    assert "The user rejected the plan: revise the plan. Revise it." in plugin.pre_llm_call()["context"]

    plan = tmp_path / ".hermes" / "plans" / "2026-09-26_tab-8.md"
    assert plugin.pre_tool_call("write_file", {"path": str(plan), "content": "x"}) is None
    plan.write_text("x", encoding="utf-8")
    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "Plan approved" in plugin.command("approve")
    session_env["HERMES_UI_SESSION_ID"] = "desktop-tab-8"
    assert "The user approved the plan" in plugin.pre_llm_call()["context"]
    assert plugin.pre_tool_call("terminal", {}) is None

    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "Plan mode is on" in plugin.command("on after approval")
    session_env["HERMES_UI_SESSION_ID"] = "desktop-tab-8"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"
    session_env.update(
        {
            "HERMES_SESSION_KEY": "command-key-after",
            "HERMES_UI_SESSION_ID": "desktop-tab-8",
        }
    )
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"
    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "No approval note" in plugin.command("off")
    session_env["HERMES_UI_SESSION_ID"] = "desktop-tab-8"
    assert plugin.pre_tool_call("terminal", {}) is None


def test_rotated_tui_command_refuses_before_the_next_hook_without_guessing(
    session_env, plugin, tmp_path
):
    session_env.update(
        {
            "HERMES_SESSION_KEY": "rotation-command-before",
            "HERMES_SESSION_SOURCE": "tui",
            "HERMES_UI_SESSION_ID": "",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    assert "Plan mode is on" in plugin.command("on immediate rotation")
    session_env["HERMES_UI_SESSION_ID"] = "rotation-ui-tab"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"

    session_env.update(
        {
            "HERMES_SESSION_KEY": "rotation-command-after",
            "HERMES_UI_SESSION_ID": "",
        }
    )
    status = plugin.command("status")
    assert "Plan mode: unresolved" in status and "is active" in status
    assert "will not guess across tabs" in plugin.command("on second state")
    assert plugin._load_state("sk:rotation-command-after") == {}
    response = plugin.command("off")
    assert "refused" in response.lower()
    assert "will not guess across tabs" in response
    assert "ordinary turn" not in response.lower()

    session_env["HERMES_UI_SESSION_ID"] = "rotation-ui-tab"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"

    session_env["HERMES_SESSION_KEY"] = "rotation-command-after"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"
    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "No approval note" in plugin.command("off")
    session_env["HERMES_UI_SESSION_ID"] = "rotation-ui-tab"
    assert plugin.pre_tool_call("terminal", {}) is None


def test_unrelated_gateway_command_is_not_treated_as_rotated_ui_key(
    session_env, plugin, tmp_path
):
    session_env.update(
        {
            "HERMES_SESSION_KEY": "ui-command-key",
            "HERMES_SESSION_SOURCE": "tui",
            "HERMES_UI_SESSION_ID": "",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    assert "Plan mode is on" in plugin.command("on ui session")
    session_env["HERMES_UI_SESSION_ID"] = "ui-tab"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"

    session_env.update(
        {
            "HERMES_SESSION_KEY": "telegram-session-key",
            "HERMES_SESSION_SOURCE": "telegram",
            "HERMES_UI_SESSION_ID": "",
        }
    )

    assert "Plan mode: off" in plugin.command("status")


def test_reenabling_linked_tui_state_preserves_command_links(
    session_env, plugin, tmp_path
):
    session_env.update(
        {
            "HERMES_SESSION_KEY": "reenable-command-key",
            "HERMES_SESSION_SOURCE": "tui",
            "HERMES_UI_SESSION_ID": "",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    assert "Plan mode is on" in plugin.command("on first")
    session_env["HERMES_UI_SESSION_ID"] = "reenable-ui-tab"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"

    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "Plan mode is on" in plugin.command("on second")
    assert "No approval note" in plugin.command("off")

    session_env["HERMES_UI_SESSION_ID"] = "reenable-ui-tab"
    assert plugin.pre_tool_call("terminal", {}) is None


def test_evicted_rotation_alias_storage_is_cleared(session_env, plugin, tmp_path):
    session_env.update({"HERMES_SESSION_KEY": "rot-0", "HERMES_SESSION_SOURCE": "tui"})
    session_env.update({"HERMES_UI_SESSION_ID": "", "TERMINAL_CWD": str(tmp_path)})
    assert "Plan mode is on" in plugin.command("on many rotations")
    session_env["HERMES_UI_SESSION_ID"] = "rot-tab"
    for index in range(257):  # 257 linked aliases evict rot-0 from the 256 cap
        session_env["HERMES_SESSION_KEY"] = f"rot-{index}"
        assert plugin.pre_tool_call("terminal", {})["action"] == "block"
    assert plugin._load_state("sk:rot-0") == {}

    plugin.on_session_reset(platform="tui")
    session_env.update(
        {"HERMES_SESSION_KEY": "", "HERMES_UI_SESSION_ID": "", "HERMES_SESSION_PLATFORM": "telegram"}
    )
    assert plugin.pre_tool_call("terminal", {}) is None


@pytest.mark.xfail(
    strict=True,
    reason=(
        "compression.in_place=false can rotate the key before Hermes exposes a "
        "stable UI id or public old-to-new mapping"
    ),
)
def test_nondefault_rotating_compression_before_first_turn_loses_plan_state(
    session_env, plugin, tmp_path
):
    session_env.update(
        {
            "HERMES_SESSION_KEY": "pre-compression-key",
            "HERMES_SESSION_SOURCE": "tui",
            "HERMES_UI_SESSION_ID": "",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    assert "Plan mode is on" in plugin.command("on before compression")

    # compression.in_place=false rotates the runtime session key before any
    # bound hook has had a chance to link the stable UI id to the command key.
    session_env.update(
        {
            "HERMES_SESSION_KEY": "post-compression-key",
            "HERMES_UI_SESSION_ID": "compression-ui-tab",
        }
    )

    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"


def test_session_reset_clears_cli_state(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plugin.on_session_reset(platform="cli")
    assert plugin.pre_tool_call("terminal", {}) is None


def test_cli_reset_clears_fallback_after_a_later_turn_derives_session_key(
    plugin, session_env, tmp_path
):
    session_env.clear()
    session_env.update(
        {
            "HERMES_SESSION_SOURCE": "cli",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    assert "Plan mode is on" in plugin.command("on cli fallback reset")
    session_env["HERMES_SESSION_KEY"] = "later-cli-turn-key"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"

    plugin.on_session_reset(platform="cli")

    assert plugin.pre_tool_call("terminal", {}) is None


def test_tui_reset_clears_adopted_ui_and_linked_command_states(
    plugin, session_env, tmp_path
):
    session_env.update(
        {
            "HERMES_SESSION_KEY": "reset-command-key",
            "HERMES_SESSION_SOURCE": "tui",
            "HERMES_UI_SESSION_ID": "",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    plugin.command("on")
    session_env["HERMES_UI_SESSION_ID"] = "reset-ui-tab"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"

    plugin.on_session_reset(platform="tui")

    assert plugin.pre_tool_call("terminal", {}) is None


def test_session_finalize_clears_cli_state(plugin, session_env, tmp_path):
    session_env.clear()
    session_env.update(
        {
            "HERMES_SESSION_SOURCE": "cli",
            "TERMINAL_CWD": str(tmp_path),
        }
    )
    plugin.command("on")
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"

    plugin.on_session_finalize(platform="cli")

    assert plugin.pre_tool_call("terminal", {}) is None


def test_session_finalize_never_clears_matching_gateway_session(
    plugin, session_env, tmp_path
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plugin.pre_tool_call("read_file", {}, session_id="gateway-session-id")
    session_env["HERMES_SESSION_KEY"] = ""
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"

    plugin.on_session_finalize(
        platform="telegram", session_id="gateway-session-id"
    )

    session_env["HERMES_SESSION_KEY"] = "unit-session"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"


def test_dead_cli_pid_state_is_pruned(plugin, session_env):
    dead_pid = 99_999_999
    dead_key = f"cli:{dead_pid}"
    plugin._save_state(
        dead_key,
        {"active": True, "plans_dir": "/tmp/unused", "cli_pid": dead_pid},
    )
    session_env.clear()
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"

    assert plugin.pre_tool_call("read_file", {"path": "/tmp/x"}) is None
    assert plugin._load_state(dead_key) == {}


def test_pid_liveness_never_signals_current_or_windows_processes(
    plugin, monkeypatch
):
    calls = []
    monkeypatch.setattr(plugin_mod.os, "kill", lambda pid, sig: calls.append((pid, sig)))

    assert plugin._pid_is_alive(os.getpid()) is True
    assert calls == []

    monkeypatch.setattr(plugin_mod.os, "name", "nt")
    assert plugin._pid_is_alive(12345) is True
    assert calls == []


def test_unbound_gateway_reset_clears_unique_active_session(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plugin.pre_tool_call("read_file", {}, session_id="old-session-id")
    session_env["HERMES_SESSION_KEY"] = ""
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"
    plugin.on_session_reset(platform="telegram", old_session_id="old-session-id")
    session_env["HERMES_SESSION_KEY"] = "unit-session"
    assert plugin.pre_tool_call("terminal", {}) is None


def test_unbound_reset_without_match_preserves_only_active_session(
    plugin, session_env, tmp_path
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    session_env["HERMES_SESSION_KEY"] = ""
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"

    plugin.on_session_reset(platform="telegram", old_session_id="another-session")

    session_env["HERMES_SESSION_KEY"] = "unit-session"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"


def test_unbound_gateway_reset_never_guesses_among_active_sessions(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    session_env["HERMES_SESSION_KEY"] = "second-session"
    plugin.command("on")
    session_env["HERMES_SESSION_KEY"] = ""
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"
    plugin.on_session_reset(platform="telegram", old_session_id="unknown")
    session_env["HERMES_SESSION_KEY"] = "unit-session"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"
    session_env["HERMES_SESSION_KEY"] = "second-session"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"


def test_custom_home_profile_helper_failure_is_unknown_not_default(monkeypatch, tmp_path):
    """A raising get_active_profile_name must not fall back to "default" (review N3)."""
    fake_profiles = ModuleType("hermes_cli.profiles")

    def _boom():
        raise RuntimeError("helper unavailable")

    fake_profiles.get_active_profile_name = _boom
    fake_cli = sys.modules.get("hermes_cli") or ModuleType("hermes_cli")
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", fake_profiles)

    assert PlanModePlugin._profile_name_for_home(tmp_path / "custom-home") is None
    assert PlanModePlugin._profile_name_for_home(tmp_path / "profiles" / "eva") == "eva"
