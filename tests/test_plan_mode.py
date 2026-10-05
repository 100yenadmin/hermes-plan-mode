from __future__ import annotations

import json
import os
import re
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
        self.tools = {}
        self.sections = {}
        self.command_hints = {}
        self.injected = []
        self.inject_result = True

    def register_command(self, name, handler, description="", args_hint=""):
        self.commands[name] = handler
        self.command_hints[name] = args_hint

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kwargs}

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_system_prompt_section(self, id, content, **kwargs):
        self.sections[id] = {"content": content, **kwargs}

    def inject_message(self, content, **kwargs):
        self.injected.append((content, kwargs))
        return self.inject_result

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
    assert set(plugin.ctx.tools) == {"plan_mode"}
    assert plugin.ctx.tools["plan_mode"]["toolset"] == "plan-mode"
    assert set(plugin.ctx.hooks) == {
        "pre_tool_call",
        "post_tool_call",
        "pre_llm_call",
        "transform_llm_output",
        "pre_approval_request",
        "post_approval_response",
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
    assert approve_note["context"].startswith(f"The user approved the plan at {plan}. Implement it now.\n\nExecuting the approved plan")
    assert plugin.pre_llm_call()["context"].startswith("Executing the approved plan")
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


@pytest.mark.parametrize("reactivate", [False, True])
def test_plan_write_is_tracked_only_by_the_activation_that_allowed_it(
    plugin, session_env, tmp_path, reactivate
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on first activation")
    plan = tmp_path / ".hermes" / "plans" / "2026-09-26_in-flight.md"
    args, ids = {"path": str(plan), "content": "# Plan\n"}, {"session_id": "s1", "tool_call_id": "c1"}
    assert plugin.pre_tool_call("write_file", args, **ids) is None
    plan.write_text("# Plan\n", encoding="utf-8")
    if reactivate:  # off -> on while the write is in flight
        plugin.command("off")
        plugin.command("on second activation")
    plugin.post_tool_call("write_file", args, status="ok", **ids)

    response = plugin.command("approve")
    if reactivate:
        assert "no tracked plan file" in response
    else:
        assert str(plan) in response


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


# --- 0.2.0: agent-callable plan_mode tool -----------------------------------


def _tool(plugin, **args):
    return json.loads(plugin.ctx.tools["plan_mode"]["handler"](args, session_id="s1"))["message"]


def _snapshot(plugin):
    return {key: dict(value) if isinstance(value, dict) else value
            for key, value in plugin.ctx.state.values.items()}


def test_t1_agent_tool_on_enforces_plan_only_writes(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plans = tmp_path / ".hermes" / "plans"

    reply = _tool(plugin, action="on", reason="draft the rollout")

    assert "Plan mode is on" in reply and str(plans) in reply
    assert plugin._load_state("sk:unit-session")["entered_by"] == "agent"
    outside = {"path": str(tmp_path / "outside.md"), "content": "x"}
    assert plugin.pre_tool_call("write_file", outside)["action"] == "block"
    plan = {"path": str(plans / "2026-09-26_120000-rollout.md"), "content": "# Plan"}
    assert plugin.pre_tool_call("write_file", plan) is None
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"
    assert plugin.pre_tool_call("plan_mode", {"action": "status"}) is None


def test_t2_agent_tool_off_ends_its_own_plan_mode_and_keeps_files(
    plugin, session_env, tmp_path
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    _tool(plugin, action="on")
    plan = tmp_path / ".hermes" / "plans" / "2026-09-26_120000-keep.md"
    assert plugin.pre_tool_call("write_file", {"path": str(plan), "content": "#"}) is None
    plan.write_text("# Plan\n", encoding="utf-8")

    reply = _tool(plugin, action="off")

    assert "Plan mode is off" in reply
    state = plugin._load_state("sk:unit-session")
    assert not state.get("active") and "activation_id" not in state
    assert "entered_by" not in state
    assert plan.read_text(encoding="utf-8") == "# Plan\n"
    assert plugin.pre_tool_call("terminal", {"command": "pwd"}) is None
    assert plugin.pre_llm_call() is None


@pytest.mark.parametrize("provenance", ["user", "legacy", "stale-agent"])
def test_t3_agent_tool_off_cannot_end_user_plan_mode(
    plugin, session_env, tmp_path, provenance
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    assert "Plan mode is on" in plugin.command("on")
    state = plugin._load_state("sk:unit-session")
    if provenance == "legacy":  # a 0.1.x state carries no provenance
        state.pop("entered_by", None)
    elif provenance == "stale-agent":  # agent marker from an earlier activation
        state.update(entered_by="agent", agent_activation_id="earlier-activation")
    plugin._save_state("sk:unit-session", state)
    before = _snapshot(plugin)

    reply = _tool(plugin, action="off")

    assert reply == (
        "Plan mode was entered by the user; only /planmode approve, reject or off can end it."
    )
    assert _snapshot(plugin) == before
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"


@pytest.mark.parametrize("action", ["approve", "reject", "APPROVE", "", "bogus"])
def test_t4_agent_tool_cannot_approve_or_reject(plugin, session_env, tmp_path, action):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    schema = plugin.ctx.tools["plan_mode"]["schema"]
    assert schema["parameters"]["properties"]["action"]["enum"] == ["on", "status", "off", "submit"]
    _tool(plugin, action="on")
    plan = tmp_path / ".hermes" / "plans" / "2026-09-26_120000-plan.md"
    assert plugin.pre_tool_call("write_file", {"path": str(plan), "content": "#"}) is None
    plan.write_text("#", encoding="utf-8")
    before = _snapshot(plugin)

    reply = _tool(plugin, action=action)

    assert "unsupported" in reply.lower() and "/planmode approve" in reply
    assert _snapshot(plugin) == before
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"


def test_t5_agent_tool_on_while_user_plan_mode_is_active_reports_status(
    plugin, session_env, tmp_path
):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on user task")
    before = _snapshot(plugin)

    reply = _tool(plugin, action="on", reason="agent task")

    assert reply.startswith("Plan mode: on")
    assert _snapshot(plugin) == before
    assert plugin._load_state("sk:unit-session")["entered_by"] == "user"
    assert _tool(plugin, action="status") == plugin.command("status")


def test_t6_agent_tool_refuses_without_session_identity(
    session_env, plugin, monkeypatch, tmp_path
):
    session_env.clear()
    session_env.update({"HERMES_SESSION_PLATFORM": "telegram", "TERMINAL_CWD": str(tmp_path)})
    monkeypatch.setattr(plugin_mod, "_session_context_is_engaged", lambda: True)

    reply = _tool(plugin, action="on")

    assert reply.startswith("Plan mode activation was refused")
    assert "session binding" in reply
    assert plugin.ctx.state.values == {}
    assert not (tmp_path / ".hermes").exists()

    monkeypatch.setattr(plugin_mod, "_session_reader", lambda: None)
    assert "unavailable" in _tool(plugin, action="on")
    assert plugin.ctx.state.values == {}


def test_t7_turn_note_differs_by_provenance(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    agent_note = (
        "You entered plan mode yourself: if the task turns out not to need a plan, you may call "
        "plan_mode(action='off') before submitting. Once you submit a plan, only the user can end plan mode."
    )
    _tool(plugin, action="on")
    note = plugin.pre_llm_call()["context"]
    assert "Plan mode is ON:" in note and note.endswith(agent_note)

    _tool(plugin, action="off")
    plugin.command("on")
    note = plugin.pre_llm_call()["context"]
    assert "Plan mode is ON:" in note and agent_note not in note


def test_agent_tool_in_ui_turn_is_reachable_by_the_tabs_slash_commands(
    plugin, session_env, tmp_path
):
    session_env.clear()
    session_env.update({
        "HERMES_SESSION_SOURCE": "tui", "HERMES_SESSION_KEY": "tab-sk",
        "HERMES_UI_SESSION_ID": "tab-1", "TERMINAL_CWD": str(tmp_path),
    })
    assert "Plan mode is on" in _tool(plugin, action="on")

    del session_env["HERMES_UI_SESSION_ID"]  # TUI slash commands bind only the sk key
    assert plugin.command("status").startswith("Plan mode: on")
    assert "Plan mode is off" in plugin.command("off")
    session_env["HERMES_UI_SESSION_ID"] = "tab-1"
    assert plugin.pre_tool_call("terminal", {"command": "pwd"}) is None


def test_t9_user_reject_hands_agent_plan_mode_to_the_user(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    _tool(plugin, action="on")
    activation = plugin._load_state("sk:unit-session")["activation_id"]

    reply = plugin.command("reject add rollback steps")

    assert reply.endswith(
        "Plan mode is now user-owned; only /planmode approve, reject or off can end it."
    )
    state = plugin._load_state("sk:unit-session")
    assert state["entered_by"] == "user" and "agent_activation_id" not in state
    assert state["activation_id"] == activation
    assert _tool(plugin, action="off") == (
        "Plan mode was entered by the user; only /planmode approve, reject or off can end it."
    )
    assert plugin._load_state("sk:unit-session")["active"] is True
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"


def test_user_approve_clears_agent_provenance(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    _tool(plugin, action="on")
    plan = tmp_path / ".hermes" / "plans" / "2026-09-26_120000-plan.md"
    assert plugin.pre_tool_call("write_file", {"path": str(plan), "content": "#"}) is None
    plan.write_text("#", encoding="utf-8")

    assert "Plan approved" in plugin.command("approve")

    state = plugin._load_state("sk:unit-session")
    assert not {"activation_id", "entered_by", "agent_activation_id"} & set(state)
    plugin.command("on")
    assert _tool(plugin, action="off") == (
        "Plan mode was entered by the user; only /planmode approve, reject or off can end it."
    )
    assert plugin._load_state("sk:unit-session")["active"] is True


# U1: submit uses a recorded human decision, never an automatic tool allowance.
@pytest.fixture
def submitted_plan(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    assert "Plan mode is on" in plugin.command("on approval proof")
    path = tmp_path / ".hermes" / "plans" / "plan.md"
    args = {"path": str(path), "content": "# Approval plan\n\n## Inspect\n## Implement\n## Verify\n"}
    ids = {"session_id": "s1", "tool_call_id": "write-plan"}
    assert plugin.pre_tool_call("write_file", args, **ids) is None
    path.write_text(args["content"], encoding="utf-8")
    plugin.post_tool_call("write_file", args, status="ok", **ids)
    return path


def _submit(plugin, call_id="submit-1", **args):
    directive = plugin.pre_tool_call(
        "plan_mode", {"action": "submit", **args}, session_id="s1", tool_call_id=call_id)
    assert directive and directive["action"] == "approve"
    return directive


def _decision(plugin, directive, choice="once", **kwargs):
    plugin.ctx.hooks["post_approval_response"](
        pattern_key="plugin_rule:" + directive["rule_key"], choice=choice,
        tool_call_id=kwargs.pop("tool_call_id", "submit-1"), **kwargs)


def _submit_result(plugin):
    return json.loads(plugin.tool({"action": "submit"}, tool_call_id="submit-1", session_id="s1"))


def test_u1_schema_and_search_description(plugin):
    tool = plugin.ctx.tools["plan_mode"]
    props = tool["schema"]["parameters"]["properties"]
    assert props["action"]["enum"] == ["on", "status", "off", "submit"]
    assert {"path", "summary"} <= props.keys()
    description = tool["description"]
    assert len(description) < 900
    assert "submit" in description[:500] and "ALONE" in description[:500]
    assert "user" in description[:500] and "off" in description[:500]


def test_u1_submit_rule_key_unique_and_revision_monotonic(plugin, submitted_plan):
    first = _submit(plugin, path=submitted_plan.name, summary="Approval proof")
    assert re.fullmatch(r"plan-mode:[a-z0-9]{8}:1:[a-f0-9]{8}:[a-f0-9]{8}", first["rule_key"])
    assert plugin._load_state("sk:unit-session")["submission"]["path"] == str(submitted_plan)
    assert "awaiting approval (rev 1)" in plugin.command("status")
    _submit_result(plugin)  # no human: consume and permit resubmission
    second = _submit(plugin, call_id="submit-2")
    assert second["rule_key"] != first["rule_key"]
    assert plugin._load_state("sk:unit-session")["revision"] == 2


@pytest.mark.parametrize("platform", ["telegram", "SLACK", "discord"])
def test_u1_compact_approval_text(platform, tmp_path):
    from plan_mode.approval import approval_text
    text = "# **Ship it**\n\n1. **Inspect**\n   - nested detail\n2. `Implement`\n## Verify\n" + "## Long step " + "x" * 400
    result = approval_text(text, tmp_path / "plan.md", 3, platform)
    assert len(result) <= 250
    assert result.startswith("Plan rev 3: Ship it")
    assert "1. Inspect\n2. Implement\n3. Verify" in result
    assert "nested detail" not in result
    assert result.endswith("…")
    assert not any(marker in result for marker in "#*`")
    titled = approval_text(text, tmp_path / "plan.md", 3, platform, "  My\n title  ")
    assert titled.startswith("Plan rev 3: My title")
    fallback = approval_text("first line\nsecond line", tmp_path / "plan.md", 1, platform)
    assert "Plan rev 1: plan.md\n1. first line\n2. second line" == fallback


def test_u1_full_approval_text_and_truncation(tmp_path):
    from plan_mode.approval import approval_text
    path = tmp_path / "plan.md"
    text = "# Plan\n\nWhole detailed plan"
    for platform in ("", "tui", "desktop", "mattermost"):
        assert approval_text(text, path, 4, platform).endswith("\n\n" + text)
    result = approval_text("x" * 4000, path, 4, "mattermost")
    assert len(result) <= 3500
    assert result.endswith(f"\n… (truncated; full plan: {path})")


def test_u1_digest_cap_decode_and_rule_sanitization(tmp_path):
    from plan_mode.approval import make_rule_key, plan_digest
    path = tmp_path / "plan.md"
    path.write_bytes(b"a\xff")
    digest, text = plan_digest(path)
    assert len(digest) == 64 and text == "a\ufffd"
    assert re.fullmatch(r"[a-z0-9:-]+", make_rule_key("A-*?[", 1, digest))
    path.write_bytes(b"x" * (1024 * 1024))
    assert len(plan_digest(path)[1]) == 1024 * 1024
    path.write_bytes(b"x" * (1024 * 1024 + 1))
    with pytest.raises(ValueError):
        plan_digest(path)


@pytest.mark.parametrize("choice", ["once", "session", "always"])
def test_u1_human_approval_turns_off_in_same_call(plugin, submitted_plan, choice):
    directive = _submit(plugin)
    plugin.ctx.hooks["pre_approval_request"](pattern_key="plugin_rule:" + directive["rule_key"])
    _decision(plugin, directive, choice)
    result = _submit_result(plugin)
    assert result["approved"] is True and result["revision"] == 1
    assert result["path"] == str(submitted_plan) and "todo_list" in result["message"]
    state = plugin._load_state("sk:unit-session")
    assert state["active"] is False and state["phase"] == "executing"
    assert state["approved_revision"] == 1 and state["approved_at"]
    assert state["pending_note"] == "" and state["submission"]["status"] == "approved"
    assert not {"activation_id", "entered_by", "agent_activation_id"} & state.keys()
    assert plugin.pre_tool_call("terminal", {}) is None
    assert "executing (rev 1:" in plugin.command("status")
    assert "nothing awaiting approval" in _submit_result(plugin)["message"].lower()
    assert plugin._ledger.take(directive["rule_key"]) is None


@pytest.mark.parametrize("decision", ["cancelled", "mismatch", "unrelated", "none"])
def test_u1_only_correlated_human_decision_approves(plugin, submitted_plan, decision):
    directive = _submit(plugin)
    if decision == "cancelled":
        _decision(plugin, directive, cancelled="turn ended")
    elif decision == "mismatch":
        _decision(plugin, directive, tool_call_id="another-call")
    elif decision == "unrelated":
        plugin.ctx.hooks["post_approval_response"](pattern_key="plugin_rule:other", choice="once")
    result = _submit_result(plugin)
    assert "No human approval was recorded" in result["message"]
    assert "approvals.mode: off" in result["message"]
    assert plugin._load_state("sk:unit-session")["submission"]["status"] == "awaiting"
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"
    assert plugin._ledger.take(directive["rule_key"]) is None


def test_u1_yolo_fallback_typed_approval_pins_revision_and_injects(plugin, submitted_plan):
    _submit(plugin)
    assert "No human approval" in _submit_result(plugin)["message"]
    newer = submitted_plan.with_name("newer.md")
    plugin.pre_tool_call("write_file", {"path": str(newer)})
    newer.write_text("# Newer", encoding="utf-8")
    reply = plugin.command("approve")
    assert str(submitted_plan) in reply and str(newer) not in reply
    assert reply.endswith("Starting implementation now.")
    assert plugin.ctx.injected == [(f"Implement the approved plan at {submitted_plan}.", {"session_key": "unit-session"})]
    assert plugin._load_state("sk:unit-session")["approved_revision"] == 1


@pytest.mark.parametrize("inject_kind", ["false", "missing", "raises", "cli"])
def test_u1_typed_approval_injection_fallback(plugin, session_env, submitted_plan, inject_kind):
    if inject_kind == "false":
        plugin.ctx.inject_result = False
    elif inject_kind == "missing":
        plugin.ctx.inject_message = None
    elif inject_kind == "raises":
        plugin.ctx.inject_message = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("unavailable"))
    else:
        session_env["HERMES_SESSION_KEY"] = ""
        state = plugin._load_state("sk:unit-session")
        plugin._save_state(f"cli:{os.getpid()}", state)
    reply = plugin.command("approve")
    assert reply.endswith("Starting implementation now." if inject_kind == "cli" else "Send any message to start.")
    if inject_kind == "cli":
        assert plugin.ctx.injected == [(f"Implement the approved plan at {submitted_plan}.", {})]


def test_u1_edited_plan_is_stale_even_after_human_approval(plugin, submitted_plan):
    directive = _submit(plugin)
    _decision(plugin, directive)
    submitted_plan.write_text("# Edited plan", encoding="utf-8")
    assert "changed after rev 1 was submitted" in _submit_result(plugin)["message"]
    state = plugin._load_state("sk:unit-session")
    assert state["active"] and state["submission"]["status"] == "stale"


@pytest.mark.parametrize("choice", ["deny", "timeout", "cancelled", "notify_failed", None])
def test_u1_blocked_submit_note_once_and_typed_fallback(plugin, submitted_plan, choice):
    directive = _submit(plugin)
    if choice is not None:
        _decision(plugin, directive, choice)
    plugin.post_tool_call("plan_mode", {"action": "submit"}, tool_call_id="submit-1", session_id="s1", status="blocked")
    state = plugin._load_state("sk:unit-session")
    assert state["active"]
    expected_status = "rejected" if choice == "deny" else "awaiting"
    assert state["submission"]["status"] == expected_status
    assert f"Last submission: rev 1 {expected_status}" in plugin.command("status")
    expected = (
        "The user did not approve plan rev 1. A plan denial is review feedback, not a safety refusal: "
        "if no reason was given, ask what to change; then revise the plan file and submit a complete new revision."
        if choice == "deny" else
        "Plan rev 1 was not answered (the approval prompt timed out or was withdrawn). "
        "Tell the user they can run /planmode approve to approve rev 1, or reply with changes."
    )
    assert expected in plugin.pre_llm_call()["context"]
    assert expected not in plugin.pre_llm_call()["context"]
    assert plugin._ledger.take(directive["rule_key"]) is None
    if choice != "deny":
        assert "Plan approved" in plugin.command("approve")


def test_u1_typed_approval_refuses_changed_submission_but_explicit_file_works(plugin, submitted_plan):
    _submit(plugin)
    plugin.post_tool_call("plan_mode", {"action": "submit"}, tool_call_id="submit-1", status="blocked")
    submitted_plan.write_text("# Changed", encoding="utf-8")
    reply = plugin.command("approve")
    assert "Plan rev 1 changed after it was submitted" in reply
    assert plugin._load_state("sk:unit-session")["active"]
    assert "Plan approved" in plugin.command(f"approve {submitted_plan.name}")


def test_u1_second_inflight_submit_blocks_but_restart_allows(plugin, submitted_plan):
    from plan_mode.approval import DecisionLedger
    first = _submit(plugin)
    block = plugin.pre_tool_call("plan_mode", {"action": "submit"}, tool_call_id="submit-2")
    assert block["action"] == "block" and "already open" in block["message"]
    # A blocked duplicate must not consume the original prompt's decision.
    plugin.post_tool_call("plan_mode", {"action": "submit"}, tool_call_id="submit-2", status="blocked")
    assert plugin._ledger.is_inflight(first["rule_key"])
    plugin._ledger = DecisionLedger()
    second = _submit(plugin, call_id="submit-2")
    assert first["rule_key"] != second["rule_key"]
    assert plugin._load_state("sk:unit-session")["revision"] == 2


@pytest.mark.parametrize("scenario", ["off", "missing-key", "unsupported", "untracked", "oversized"])
def test_u1_submit_fail_closed_with_reason(plugin, session_env, submitted_plan, tmp_path, monkeypatch, scenario):
    args = {"action": "submit"}
    if scenario == "off":
        plugin.command("off")
    elif scenario == "missing-key":
        session_env.update(HERMES_SESSION_KEY="", HERMES_SESSION_PLATFORM="telegram")
    elif scenario == "unsupported":
        monkeypatch.setattr(plugin_mod, "_session_reader", lambda: None)
    elif scenario == "untracked":
        other = tmp_path / "untracked.md"
        other.write_text("# Plan", encoding="utf-8")
        args["path"] = str(other)
    else:
        submitted_plan.write_bytes(b"x" * (1024 * 1024 + 1))
    block = plugin.pre_tool_call("plan_mode", args, tool_call_id="submit-1")
    assert block and block["action"] == "block" and block["message"]


def test_u1_on_off_clear_submission_keep_revision_and_reject_marks(plugin, submitted_plan):
    _submit(plugin)
    plugin.command("reject smaller")
    assert plugin._load_state("sk:unit-session")["submission"]["status"] == "rejected"
    plugin.command("off")
    state = plugin._load_state("sk:unit-session")
    assert "submission" not in state and "phase" not in state
    plugin.command("on")
    assert "submission" not in plugin._load_state("sk:unit-session")
    _submit(plugin)
    assert plugin._load_state("sk:unit-session")["revision"] == 2
    plugin._ledger.take(plugin._load_state("sk:unit-session")["submission"]["rule_key"])
    assert "Plan approved" in plugin.command("approve")  # restart-style pending without inflight
    plugin.command("on")
    state = plugin._load_state("sk:unit-session")
    assert not {"submission", "phase", "approved_path", "approved_revision", "approved_at"} & state.keys()
    _submit(plugin)
    assert plugin._load_state("sk:unit-session")["revision"] == 3


@pytest.mark.parametrize("event", ["reset", "finalize"])
def test_u1_session_cleanup_discards_submission_and_ledger(plugin, session_env, submitted_plan, event):
    state = plugin._load_state("sk:unit-session")
    session_env["HERMES_SESSION_KEY"] = ""
    plugin._save_state(f"cli:{os.getpid()}", state)
    directive = _submit(plugin)
    if event == "reset":
        plugin.on_session_reset(platform="cli")
    else:
        plugin.on_session_finalize(platform="cli")
    assert plugin._load_state(f"cli:{os.getpid()}") == {}
    assert plugin._ledger.take(directive["rule_key"]) is None


def test_u1_ledger_bounded_observer_safe_and_single_use():
    from plan_mode.approval import DecisionLedger, is_human_approval
    ledger = DecisionLedger()
    for index in range(257):
        ledger.mark_inflight(f"plan-mode:{index}", "call")
    assert ledger.take("plan-mode:0") is None
    key = "plan-mode:256"
    ledger.record_presented(pattern_key="plugin_rule:" + key)
    ledger.record_response(pattern_key="unrelated", choice="once")
    assert ledger.is_inflight(key)
    for bad in (None, [], 42):
        ledger.record_response(pattern_key=bad, choice="once")
        ledger.record_presented(pattern_key=bad)
    ledger.record_response(pattern_key="plugin_rule:" + key, choice="once", tool_call_id="call")
    entry = ledger.take(key)
    assert entry["presented"] and is_human_approval(entry, "call")
    assert not is_human_approval(entry, "other")
    assert is_human_approval(entry, None)
    assert ledger.take(key) is None
    ledger.record_response(pattern_key="plugin_rule:" + key, choice="once")
    assert ledger.take(key) is None  # late responses cannot recreate consumed entries


# U2: core entry, turn notes and text-first rendering.
_BUILTIN_PROMPT = (
    "[/plan — plan mode]\n\nFor this turn, you are in PLAN MODE — planning only."
    "\n\nTask to plan:\nship it\n\nOffer execution later."
)
_OVERRIDE = "This overrides the /plan instruction to offer execution: submit the plan with plan_mode instead."
_EXECUTION_FIELDS = {"phase", "approved_path", "approved_revision", "approved_at", "executing_turns", "progress"}


@pytest.mark.parametrize("prefix", ["", "[Alice] ", '[Replying to: "x"]\n\n'])
def test_u2_builtin_plan_enforced_before_first_tool(plugin, session_env, tmp_path, prefix):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    note = plugin.pre_llm_call(user_message=prefix + _BUILTIN_PROMPT)["context"]
    state = plugin._load_state("sk:unit-session")
    assert state["active"] and state["entered_by"] == "user"
    assert state["task"] == "ship it"
    assert "Plan mode is ON:" in note and note.endswith(_OVERRIDE)
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"
    assert _OVERRIDE not in plugin.pre_llm_call()["context"]


@pytest.mark.parametrize("message", ["[/plan — plan mode]", "For this turn, you are in PLAN MODE — planning only."])
def test_u2_builtin_needs_both_markers(plugin, message):
    assert plugin.pre_llm_call(user_message=message) is None
    assert not plugin._load_state("sk:unit-session").get("active")


@pytest.mark.parametrize("key", ["plan_mode.enforce_builtin_plan", "enforce_builtin_plan"])
def test_u2_builtin_can_be_disabled(plugin, key):
    plugin.ctx.settings[key] = False
    assert plugin.pre_llm_call(user_message=_BUILTIN_PROMPT) is None
    assert not plugin._load_state("sk:unit-session").get("active")


def test_u2_builtin_keeps_existing_activation_and_submission(plugin, submitted_plan):
    _submit(plugin)
    before = plugin._load_state("sk:unit-session")
    note = plugin.pre_llm_call(user_message=_BUILTIN_PROMPT)["context"]
    assert plugin._load_state("sk:unit-session") == before
    assert note.endswith(_OVERRIDE)


@pytest.mark.parametrize("task, expected", [("", ""), ("  first\nsecond\n\nignored", "first\nsecond"), ("x" * 600, "x" * 500)])
def test_u2_builtin_task_extraction(plugin, session_env, tmp_path, task, expected):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    prompt = _BUILTIN_PROMPT.split("Task to plan:")[0]
    if task:
        prompt += "Task to plan:\n" + task
    plugin.pre_llm_call(user_message=prompt)
    assert plugin._load_state("sk:unit-session")["task"] == expected


@pytest.mark.parametrize("refusal", ["profile", "identity"])
def test_u2_builtin_refusal_injects_nothing(plugin, session_env, tmp_path, monkeypatch, refusal):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    if refusal == "profile":
        plugin._registration_profile = "profile-a"
        session_env["HERMES_SESSION_PROFILE"] = "profile-b"
    else:
        session_env.clear()
        monkeypatch.setattr(plugin_mod, "_session_context_is_engaged", lambda: True)
    assert plugin.pre_llm_call(user_message=_BUILTIN_PROMPT) is None
    assert plugin.ctx.state.values == {}


def test_u2_exact_planning_note_and_pending_first(plugin, session_env, tmp_path, monkeypatch):
    from plan_mode import render as render_mod
    monkeypatch.setattr(render_mod, "plan_file_stamp", lambda now=None: "2026-10-05_120000")
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plans = tmp_path / ".hermes" / "plans"
    expected = (
        f"Plan mode is ON: only read-only tools work, and files may be written only under {plans}.\n"
        "1. Explore with read-only tools. If a requirement is genuinely ambiguous, ask with the clarify tool (up to 4 short choices, recommended first) instead of guessing.\n"
        f"2. Write the plan as Markdown to {plans}/2026-10-05_120000-<slug>.md (that timestamp is current; do not look up the time), with numbered steps.\n"
        '3. Show the complete plan in your reply, then call plan_mode(action="submit") on its own to ask the user to approve it (if plan_mode is not loaded, find it with tool_search "plan_mode"). Approval starts implementation in this same turn.\n'
        '4. Do not implement before approval and do not ask "should I proceed?" in prose. A denial is review feedback, not a refusal: revise the plan and submit a complete new revision.\n'
        "In group chats, keep secrets and private details out of the plan."
    )
    assert plugin.pre_llm_call() == {"context": expected}
    plugin.command("reject smaller")
    assert plugin.pre_llm_call() == {"context": "The user rejected the plan: smaller. Revise it.\n\n" + expected}
    assert plugin.pre_llm_call() == {"context": expected}


@pytest.fixture
def executing_plan(plugin, submitted_plan):
    directive = _submit(plugin)
    _decision(plugin, directive)
    assert _submit_result(plugin)["approved"]
    return submitted_plan


def test_u2_executing_pointer_and_turn_expiry(plugin, executing_plan):
    expected = f"Executing the approved plan {executing_plan} (rev 1). Keep todo_list statuses current; re-read the plan if your context was compacted."
    assert plugin.pre_llm_call() == {"context": expected}
    state = plugin._load_state("sk:unit-session")
    assert state["executing_turns"] == 1
    state["executing_turns"] = 99
    plugin._save_state("sk:unit-session", state)
    assert plugin.pre_llm_call() == {"context": expected}
    assert plugin._load_state("sk:unit-session")["executing_turns"] == 100
    assert plugin.pre_llm_call() is None
    assert not _EXECUTION_FIELDS & plugin._load_state("sk:unit-session").keys()


def test_u2_executing_pointer_without_revision(plugin, submitted_plan):
    plugin.command("approve")
    note = plugin.pre_llm_call()["context"]
    assert f"Executing the approved plan {submitted_plan}." in note and "(rev" not in note


@pytest.mark.parametrize("tool", ["todo_list", "todo"])
def test_u2_todo_progress_and_completion(plugin, executing_plan, tool):
    todos = [{"content": "x" * 70, "status": "in_progress"}, {"status": "completed"}, {"status": "cancelled"}]
    result = {"todos": todos, "revision": 2, "summary": {"total": 3, "completed": 1, "cancelled": 1}}
    plugin.post_tool_call(tool, {"todos": []}, status="ok", result=json.dumps(result))
    assert plugin._load_state("sk:unit-session")["progress"] == {"total": 3, "completed": 1, "cancelled": 1, "current": "x" * 60}
    todos[0]["status"] = "completed"
    result["summary"]["completed"] = 2
    plugin.post_tool_call(tool, {"todos": []}, status="ok", result=json.dumps(result))
    state = plugin._load_state("sk:unit-session")
    assert not _EXECUTION_FIELDS & state.keys()
    assert state["submission"]["status"] == "approved"
    assert plugin.pre_llm_call() is None


@pytest.mark.parametrize("result", ["bad json", "[]", "null", '{"todos": 42}', '{"summary": {"total": "bad"}}'])
def test_u2_malformed_todo_ignored(plugin, executing_plan, result):
    before = _snapshot(plugin)
    plugin.post_tool_call("todo_list", status="ok", result=result)
    assert _snapshot(plugin) == before


def test_u2_todo_empty_failed_and_planning_do_not_end_execution(plugin, executing_plan):
    plugin.post_tool_call("todo_list", status="ok", result=json.dumps({"todos": [], "summary": {"total": 0}}))
    assert plugin._load_state("sk:unit-session")["phase"] == "executing"
    before = _snapshot(plugin)
    plugin.post_tool_call("todo_list", status="error", result='{"todos": [{"status": "completed"}]}')
    assert _snapshot(plugin) == before
    plugin.command("on")
    before = _snapshot(plugin)
    plugin.post_tool_call("todo_list", status="ok", result='{"todos": [{"status": "completed"}]}')
    assert _snapshot(plugin) == before


@pytest.mark.parametrize("action", ["done", "off", "on", "reset"])
def test_u2_execution_cleanup(plugin, executing_plan, action):
    state = plugin._load_state("sk:unit-session")
    state.update(executing_turns=7, progress={"total": 2})
    plugin._save_state("sk:unit-session", state)
    if action == "reset":
        plugin.on_session_reset()
    else:
        plugin.command(action)
    assert not _EXECUTION_FIELDS & plugin._load_state("sk:unit-session").keys()


def test_u2_done_has_no_approval_effect(plugin, submitted_plan):
    _submit(plugin)
    before = _snapshot(plugin)
    assert "executing" in plugin.command("done").lower()
    assert _snapshot(plugin) == before
    assert plugin.pre_tool_call("terminal", {})["action"] == "block"


def test_u2_hint_and_command_hint_registered(plugin):
    section = plugin.ctx.sections["plan-mode.hint"]
    assert section["position"] == "after_memory"
    assert section["content"] == 'For multi-step or risky changes you can enter enforced plan mode with the plan_mode tool (action "on"; find it with tool_search) and submit a plan for the user\'s approval before building.'
    assert len(section["content"]) <= 200
    assert plugin.ctx.command_hints["planmode"] == "[on|status|show|approve|reject|done|off] [task]"


@pytest.mark.parametrize("key", ["agent_hint", "plan_mode.agent_hint"])
def test_u2_hint_opt_out(session_env, key):
    ctx = FakeContext()
    ctx.settings[key] = False
    PlanModePlugin(ctx).register()
    assert ctx.sections == {} and "plan_mode" in ctx.tools


@pytest.mark.parametrize("capability", ["missing", "raises"])
def test_u2_hint_registration_fail_open(session_env, capability):
    ctx = FakeContext()
    ctx.register_system_prompt_section = None if capability == "missing" else _raise_loader
    PlanModePlugin(ctx).register()
    assert "plan_mode" in ctx.tools and "transform_llm_output" in ctx.hooks


def test_u2_show_default_explicit_and_read_only(plugin, submitted_plan, session_env):
    assert plugin.command("show").endswith(submitted_plan.read_text())
    _submit(plugin)
    newer = submitted_plan.with_name("newer.md")
    plugin.pre_tool_call("write_file", {"path": str(newer)})
    newer.write_text("# Newer")
    before = _snapshot(plugin)
    assert plugin.command("show").startswith(f"Plan: {submitted_plan} (rev 1, pending)\n\n")
    for argument in (submitted_plan.name, str(submitted_plan)):
        assert plugin.command("show " + argument).endswith(submitted_plan.read_text())
    assert plugin.command("show newer.md").endswith("# Newer")
    session_env["HERMES_SESSION_PROFILE"] = "different-profile"
    assert plugin.command("show").endswith(submitted_plan.read_text())
    assert _snapshot(plugin) == before


def test_u2_show_no_plan_foreign_and_truncation(plugin, session_env, tmp_path):
    assert "no" in plugin.command("show").lower() and "plan" in plugin.command("show").lower()
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    path = tmp_path / ".hermes" / "plans" / "large.md"
    plugin.pre_tool_call("write_file", {"path": str(path)})
    path.write_text("# Plan\n" + "x" * 4000)
    reply = plugin.command("show")
    assert len(reply) <= 3500 and reply.endswith(f"… (truncated; full plan at {path})")
    session_env["HERMES_SESSION_KEY"] = "other-session"
    plugin.command("on")
    assert "not written by this session" in plugin.command("show " + str(path))


@pytest.mark.parametrize("platform", ["telegram", "TELEGRAM", "slack", "discord", "mattermost"])
def test_u2_planning_footer(plugin, submitted_plan, platform):
    assert plugin.transform_llm_output(response_text="Plan draft  ", platform=platform) == "Plan draft\n\n⏸ Plan mode: nothing changes until you approve the plan."


@pytest.mark.parametrize("platform", ["", "cli", "terminal", "tui", "desktop", "dashboard", "api_server", "webhook", "acp", "local", "batch", "cron"])
def test_u2_local_surfaces_no_footer(plugin, submitted_plan, platform):
    assert plugin.transform_llm_output(response_text="Draft", platform=platform) is None


def test_u2_footer_limits_opt_out_and_unresolved(plugin, submitted_plan, session_env, monkeypatch):
    assert plugin.transform_llm_output(response_text="x" * 1801, platform="telegram") is None
    assert plugin.transform_llm_output(response_text="x" * 1800, platform="telegram") is not None
    for key in ("footer", "plan_mode.footer"):
        plugin.ctx.settings[key] = "off"
        assert plugin.transform_llm_output(response_text="Draft", platform="telegram") is None
        plugin.ctx.settings.clear()
    session_env["HERMES_SESSION_KEY"] = "other-session"
    assert plugin.transform_llm_output(response_text="Draft", platform="telegram") is None
    monkeypatch.setattr(plugin, "_load_state", _raise_loader)
    assert plugin.transform_llm_output(response_text="Draft", platform="telegram") is None


def test_u2_executing_footer_progress(plugin, executing_plan):
    assert plugin.transform_llm_output(response_text="Working", platform="telegram") == "Working\n\nExecuting approved plan rev 1"
    state = plugin._load_state("sk:unit-session")
    state.pop("approved_revision")
    plugin._save_state("sk:unit-session", state)
    assert plugin.transform_llm_output(response_text="Working", platform="telegram").endswith("Executing approved plan")
    plugin.post_tool_call("todo_list", {"todos": []}, status="ok", result=json.dumps({"todos": [
        {"status": "completed"}, {"status": "cancelled"}, {"status": "in_progress", "content": "Build"}]}))
    assert plugin.transform_llm_output(response_text="Working", platform="telegram").endswith("Plan progress 2/3 · now: Build")
    plugin.post_tool_call("todo", {"todos": []}, status="ok", result=json.dumps({"todos": [{"status": "pending"}]}))
    assert plugin.transform_llm_output(response_text="Working", platform="telegram").endswith("Plan progress 0/1")


@pytest.mark.parametrize("ending", ["⏸ Plan mode: already shown", "Plan progress 1/2", "Executing approved plan rev 1"])
def test_u2_footer_never_double_appends(plugin, submitted_plan, ending):
    assert plugin.transform_llm_output(response_text="Draft\n\n" + ending + "  \n", platform="telegram") is None


def test_tool_search_meta_tools_are_allowed_but_tool_call_is_not_listed(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    assert plugin.pre_tool_call("tool_search", {"query": "plan_mode"}) is None
    assert plugin.pre_tool_call("tool_describe", {"name": "plan_mode"}) is None
    assert "tool_call" not in plugin_mod.READ_ONLY_TOOLS
    assert plugin.pre_tool_call("tool_call", {"name": "terminal", "arguments": {}})["action"] == "block"


def test_plan_file_stamp_format():
    from datetime import datetime
    from plan_mode.render import plan_file_stamp
    assert plan_file_stamp(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02_030405"


# U1 review fixes: submission ownership, legacy state, typed approval while the card is open.
def _agent_plan(plugin, session_env, tmp_path, name="plan.md"):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    _tool(plugin, action="on")
    path = tmp_path / ".hermes" / "plans" / name
    args = {"path": str(path), "content": "# Plan\n\n1. Do it\n"}
    ids = {"session_id": "s1", "tool_call_id": "write-" + name}
    assert plugin.pre_tool_call("write_file", args, **ids) is None
    path.write_text(args["content"], encoding="utf-8")
    plugin.post_tool_call("write_file", args, status="ok", **ids)
    return path


@pytest.mark.parametrize("outcome", ["deny", "timeout", "yolo"])
def test_u1_submit_hands_agent_entered_plan_mode_to_the_user(plugin, session_env, tmp_path, outcome):
    _agent_plan(plugin, session_env, tmp_path)
    assert "plan_mode(action='off')" in plugin.pre_llm_call()["context"]
    directive = _submit(plugin)
    if outcome == "yolo":
        assert "No human approval" in _submit_result(plugin)["message"]
    else:
        if outcome == "deny":
            _decision(plugin, directive, "deny")
        plugin.post_tool_call("plan_mode", {"action": "submit"}, tool_call_id="submit-1", session_id="s1", status="blocked")
    state = plugin._load_state("sk:unit-session")
    assert state["active"] and state["entered_by"] == "user" and "agent_activation_id" not in state
    assert _tool(plugin, action="off") == (
        "Plan mode was entered by the user; only /planmode approve, reject or off can end it."
    )
    assert plugin._load_state("sk:unit-session")["active"] is True
    assert "plan_mode(action='off')" not in plugin.pre_llm_call()["context"]
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"


def test_u1_legacy_state_without_activation_id_can_submit(plugin, submitted_plan):
    state = plugin._load_state("sk:unit-session")
    state.pop("activation_id")
    plugin._save_state("sk:unit-session", state)
    directive = _submit(plugin)
    assert re.fullmatch(r"plan-mode:00000000:1:[0-9a-f]{8}:[0-9a-f]{8}", directive["rule_key"])
    _decision(plugin, directive)
    assert _submit_result(plugin)["approved"] is True


def _second_plan(plugin, tmp_path):
    other = tmp_path / ".hermes" / "plans" / "zz-newer.md"
    args = {"path": str(other), "content": "# Other"}
    ids = {"session_id": "s1", "tool_call_id": "write-other"}
    assert plugin.pre_tool_call("write_file", args, **ids) is None
    other.write_text(args["content"], encoding="utf-8")
    plugin.post_tool_call("write_file", args, status="ok", **ids)
    return other


@pytest.mark.parametrize("card", ["approve", "deny", "timeout"])
def test_u1_typed_approve_while_card_open_targets_the_submission(plugin, submitted_plan, tmp_path, card):
    _second_plan(plugin, tmp_path)
    directive = _submit(plugin, path=str(submitted_plan))
    assert plugin._ledger.is_inflight(directive["rule_key"])
    assert "Plan approved" in plugin.command("approve")
    state = plugin._load_state("sk:unit-session")
    assert state["approved_path"] == str(submitted_plan) and state["approved_revision"] == 1
    if card == "approve":
        _decision(plugin, directive)
        result = _submit_result(plugin)
        assert result["approved"] is True and result["path"] == str(submitted_plan)
        assert "nothing awaiting approval" in _submit_result(plugin)["message"].lower()
    else:
        if card == "deny":
            _decision(plugin, directive, "deny")
        plugin.post_tool_call("plan_mode", {"action": "submit"}, tool_call_id="submit-1", session_id="s1", status="blocked")
    state = plugin._load_state("sk:unit-session")
    assert state["active"] is False and state["phase"] == "executing"
    assert state["submission"]["status"] == "approved" and "approved_while_open" not in state
    note = state.get("pending_note") or ""
    assert "did not approve" not in note and "was not answered" not in note


def test_u1_card_answer_after_typed_off_writes_nothing(plugin, submitted_plan):
    directive = _submit(plugin)
    plugin.command("off")
    _decision(plugin, directive, "deny")
    plugin.post_tool_call("plan_mode", {"action": "submit"}, tool_call_id="submit-1", session_id="s1", status="blocked")
    state = plugin._load_state("sk:unit-session")
    assert state["active"] is False and not state.get("pending_note")
    assert plugin.pre_llm_call() is None


def test_u1_cli_approval_text_is_one_line(tmp_path):
    from plan_mode.approval import approval_text
    text = "# Add divide\n\n## Goal\nDo it.\n\n1. Tests\n2. Code\n"
    result = approval_text(text, str(tmp_path / "p.md"), 2, "cli")
    assert "\n" not in result and len(result) < 300
    assert result.startswith("Plan rev 2 (p.md): Add divide.") and "shown above" in result


def test_u1_classic_cli_submit_uses_one_line_prompt(plugin, session_env, tmp_path):
    session_env.clear()
    session_env.update({"HERMES_SESSION_SOURCE": "cli", "TERMINAL_CWD": str(tmp_path)})
    assert "Plan mode is on" in plugin.command("on cli")
    path = tmp_path / ".hermes" / "plans" / "plan.md"
    assert plugin.pre_tool_call("write_file", {"path": str(path), "content": "#"}) is None
    path.write_text("# CLI plan\n\n1. One\n2. Two\n", encoding="utf-8")
    directive = plugin.pre_tool_call("plan_mode", {"action": "submit"}, tool_call_id="c1")
    assert directive["action"] == "approve" and "\n" not in directive["message"]
    assert "CLI plan" in directive["message"]


def test_u1_title_drops_a_leading_plan_label(tmp_path):
    from plan_mode.approval import approval_text
    result = approval_text("# Plan: Add power\n\n1. Do it\n", str(tmp_path / "p.md"), 1, "cli")
    assert result.startswith("Plan rev 1 (p.md): Add power.")
    assert approval_text("# Plan: Add power\n", str(tmp_path / "p.md"), 1, "telegram").startswith("Plan rev 1: Add power")


# U2 review fixes: the executing phase ends on every path and reaches only the chat it belongs to.
def test_u2r_gateway_unbound_reset_ends_execution(plugin, session_env, executing_plan):
    plugin.pre_llm_call(session_id="s1", platform="telegram")  # records the session id while executing
    session_env["HERMES_SESSION_KEY"] = ""
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"
    plugin.on_session_reset(platform="telegram", old_session_id="s1", new_session_id="s2")
    session_env["HERMES_SESSION_KEY"] = "unit-session"
    session_env.pop("HERMES_SESSION_PLATFORM")
    assert plugin.pre_llm_call(session_id="s2", platform="telegram") is None
    assert plugin.transform_llm_output("hello in the new chat", platform="telegram") is None
    assert plugin.ctx.state.get("executing-index", []) == []


def _tui_env(session_env, bound):
    session_env.update({"HERMES_SESSION_KEY": "tui-key", "HERMES_SESSION_SOURCE": "tui",
                        "HERMES_UI_SESSION_ID": "tab-1" if bound else ""})


def _tui_approved(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    _tui_env(session_env, True)
    assert "Plan mode is ON" in plugin.pre_llm_call(user_message=_BUILTIN_PROMPT)["context"]
    path = tmp_path / ".hermes" / "plans" / "p.md"
    args = {"path": str(path), "content": "# P\n1. a\n"}
    ids = {"session_id": "s1", "tool_call_id": "w1"}
    assert plugin.pre_tool_call("write_file", args, **ids) is None
    path.write_text(args["content"], encoding="utf-8")
    plugin.post_tool_call("write_file", args, status="ok", **ids)
    directive = _submit(plugin)
    _decision(plugin, directive)
    assert _submit_result(plugin)["approved"]


@pytest.mark.parametrize("command", ["done", "off"])
def test_u2r_tui_done_and_off_reach_the_hook_copy(plugin, session_env, tmp_path, command):
    _tui_approved(plugin, session_env, tmp_path)
    _tui_env(session_env, False)  # TUI plugin commands bind only the session key
    plugin.command(command)
    _tui_env(session_env, True)
    assert plugin.pre_llm_call() is None


def test_u2r_tui_status_follows_todo_completion(plugin, session_env, tmp_path):
    _tui_approved(plugin, session_env, tmp_path)
    plugin.post_tool_call("todo_list", {"todos": []}, status="ok",
                          result=json.dumps({"todos": [{"status": "completed"}]}))
    assert plugin.pre_llm_call() is None
    _tui_env(session_env, False)
    assert "Phase: off" in plugin.command("status")


def test_u2r_subagent_turns_get_no_footer_note_or_activation(plugin, session_env, executing_plan):
    assert plugin.transform_llm_output("child summary", platform="subagent") is None
    assert plugin.pre_llm_call(platform="subagent", user_message="do step 2") is None
    plugin.command("done")
    assert plugin.pre_llm_call(platform="subagent", user_message="Goal:\n" + _BUILTIN_PROMPT) is None
    assert not plugin._load_state("sk:unit-session").get("active")


def test_u2r_desktop_tab_resumed_from_chat_gets_no_footer(plugin, session_env, submitted_plan):
    _tui_env(session_env, True)
    plugin.pre_llm_call(user_message=_BUILTIN_PROMPT)
    assert plugin.transform_llm_output("desktop reply", platform="telegram") is None


def test_u2r_todo_read_does_not_end_execution(plugin, session_env, executing_plan):
    old = {"todos": [{"id": "1", "content": "old", "status": "completed"}]}
    plugin.post_tool_call("todo_list", {}, status="ok", result=json.dumps(old))
    assert plugin._load_state("sk:unit-session")["phase"] == "executing"


# Codex review R2 fixes.
@pytest.mark.parametrize("heading, title", [
    ("Plan: Add power", "Add power"), ("Plan — Add power", "Add power"), ("Plan-mode docs", "Plan-mode docs"),
    ("Planner fixes", "Planner fixes"),
])
def test_r2_title_keeps_compound_words(tmp_path, heading, title):
    from plan_mode.approval import approval_text
    assert approval_text(f"# {heading}\n", str(tmp_path / "p.md"), 1, "cli").startswith(f"Plan rev 1 (p.md): {title}.")


def test_r2_whatsapp_cloud_card_fits_its_body_limit(tmp_path):
    from plan_mode.approval import approval_text
    path = tmp_path / "p.md"
    result = approval_text("# Plan\n\n" + "step\n" * 600, path, 1, "whatsapp_cloud")
    assert len(result) <= 1000 and result.endswith(f"(truncated; full plan: {path})")
    assert len(approval_text("x" * 2000, path, 1, "matrix")) == 2000 + len("Plan rev 1 (p.md) — approve to start implementing, deny to keep planning.\n\n")


@pytest.mark.parametrize("platform", ["msgraph_webhook", "kanban", "tool", "codex", "gateway"])
def test_r2_non_messaging_surfaces_get_no_footer(plugin, submitted_plan, platform):
    assert plugin.transform_llm_output("reply", platform=platform) is None


def test_r2_tui_typed_approve_while_open_clears_flag_on_every_copy(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    _tui_env(session_env, True)
    plugin.pre_llm_call(user_message=_BUILTIN_PROMPT)
    path = tmp_path / ".hermes" / "plans" / "p.md"
    args = {"path": str(path), "content": "# P\n1. a\n"}
    ids = {"session_id": "s1", "tool_call_id": "w1"}
    assert plugin.pre_tool_call("write_file", args, **ids) is None
    path.write_text(args["content"], encoding="utf-8")
    plugin.post_tool_call("write_file", args, status="ok", **ids)
    directive = _submit(plugin)
    _tui_env(session_env, False)
    assert "Plan approved" in plugin.command("approve")
    _tui_env(session_env, True)
    _decision(plugin, directive, "deny")
    plugin.post_tool_call("plan_mode", {"action": "submit"}, tool_call_id="submit-1", session_id="s1", status="blocked")
    for key in ("ui:tab-1", "sk:tui-key"):
        state = plugin._load_state(key)
        assert "approved_while_open" not in state and state.get("phase") == "executing", key
