from __future__ import annotations

import os
from pathlib import Path

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
    return values


@pytest.fixture
def plugin(session_env):
    ctx = FakeContext()
    instance = PlanModePlugin(ctx)
    instance.register()
    return instance


def test_registers_exact_surface(plugin):
    assert set(plugin.ctx.commands) == {"planmode"}
    assert set(plugin.ctx.hooks) == {"pre_tool_call", "pre_llm_call", "on_session_reset"}


def test_command_state_machine_and_one_shot_notes(plugin, session_env, tmp_path, monkeypatch):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    monkeypatch.chdir(tmp_path)

    response = plugin.command("on draft the feature")
    plans_dir = tmp_path / ".hermes" / "plans"
    assert "Plan mode is on" in response
    assert str(plans_dir) in response
    assert "Plan mode: on" in plugin.command("status")

    plan = plans_dir / "2026-09-23_feature.md"
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


def test_session_reset_clears_cli_state(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plugin.on_session_reset(platform="cli")
    assert plugin.pre_tool_call("terminal", {}) is None


def test_unbound_gateway_reset_clears_unique_active_session(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    plugin.pre_tool_call("read_file", {}, session_id="old-session-id")
    session_env["HERMES_SESSION_KEY"] = ""
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"
    plugin.on_session_reset(platform="telegram", old_session_id="old-session-id")
    session_env["HERMES_SESSION_KEY"] = "unit-session"
    assert plugin.pre_tool_call("terminal", {}) is None


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
