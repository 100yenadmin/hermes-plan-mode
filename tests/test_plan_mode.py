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


def test_on_falls_back_to_process_cwd_for_invalid_terminal_cwd(plugin, session_env, tmp_path, monkeypatch):
    session_env["TERMINAL_CWD"] = "relative/missing"
    monkeypatch.chdir(tmp_path)
    response = plugin.command("on")
    assert str(tmp_path / ".hermes" / "plans") in response


def test_read_allowlist_and_unknown_blocks(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    assert plugin.pre_tool_call("read_file", {"path": "/tmp/x"}) is None
    assert plugin.pre_tool_call("browser_snapshot", {}) is None
    assert plugin.pre_tool_call("terminal", {"command": "pwd"})["action"] == "block"
    assert plugin.pre_tool_call("mcp_linear_update_issue", {})["action"] == "block"
    assert plugin.pre_tool_call("totally_new_tool", {})["action"] == "block"


def test_skill_view_is_blocked_when_inline_shell_is_enabled(
    plugin, session_env, tmp_path, monkeypatch
):
    home = tmp_path / "hermes-home"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text("skills:\n  inline_shell: true\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")

    blocked = plugin.pre_tool_call("skill_view", {"name": "unsafe-skill"})
    assert blocked["action"] == "block"
    assert "inline_shell" in blocked["message"]

    config.write_text("skills:\n  inline_shell: false\n", encoding="utf-8")
    assert plugin.pre_tool_call("skill_view", {"name": "safe-skill"}) is None


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


def test_missing_non_cli_key_overblocks_when_any_session_active(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    session_env["HERMES_SESSION_KEY"] = ""
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"
    result = plugin.pre_tool_call("read_file", {"path": "/tmp/x"})
    assert result["action"] == "block"
    assert "could not be derived" in result["message"]


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


def test_unbound_server_surface_refuses_activation(session_env, plugin, monkeypatch):
    session_env.clear()
    monkeypatch.setitem(sys.modules, "tui_gateway.server", ModuleType("tui_gateway.server"))

    response = plugin.command("on")

    assert "refused" in response.lower()
    assert "session binding" in response.lower()


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


def test_session_reset_clears_cli_state(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plugin.on_session_reset(platform="cli")
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
