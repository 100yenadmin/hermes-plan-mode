from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib
from importlib.metadata import version
import inspect
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from types import ModuleType

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_plugin(destination: Path) -> None:
    shutil.copytree(
        REPO_ROOT,
        destination,
        ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__", "*.pyc"),
    )


def test_pinned_upstream_session_seam_is_importable_and_bound_around_commands():
    pytest.importorskip("hermes_cli.plugins")
    runtime_version = tuple(int(part) for part in version("hermes-agent").split(".")[:3])
    if runtime_version < (0, 21, 4):
        pytest.skip("source-site assertion targets pinned upstream Hermes 0.21.4+")
    from gateway.session_context import get_session_env
    from gateway.run_inbound import GatewayInboundMixin

    assert callable(get_session_env)
    source = inspect.getsource(GatewayInboundMixin._hm_dispatch_quick_and_plugin_commands)
    assert "handler reading get_session_env()" in source
    assert "with self._session_env_scope(_plugin_context):" in source


def test_real_cli_like_process_works_after_gateway_run_import(tmp_path, monkeypatch):
    pytest.importorskip("hermes_cli.plugins")
    home = tmp_path / "hermes-home"
    workspace = tmp_path / "workspace"
    empty_bundled = tmp_path / "empty-bundled"
    workspace.mkdir()
    empty_bundled.mkdir()
    _copy_plugin(home / "plugins" / "plan-mode")
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - plan-mode\n  load_timeout_seconds: 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(empty_bundled))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "0")
    monkeypatch.setenv("TERMINAL_CWD", str(workspace))
    for key in (
        "HERMES_SESSION_KEY",
        "HERMES_SESSION_SOURCE",
        "HERMES_SESSION_PLATFORM",
        "HERMES_UI_SESSION_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    importlib.import_module("gateway.run")
    from gateway.session_context import session_context_engaged
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_cli import plugins

    home_token = set_hermes_home_override(str(home))
    try:
        assert session_context_engaged() is False
        plugins._reset_plugin_managers_for_tests()
        plugins.get_plugin_manager().discover_and_load()
        response = plugins.get_plugin_command_handler("planmode")("on real cli")

        assert "Plan mode is on" in response
    finally:
        plugins._reset_plugin_managers_for_tests()
        reset_hermes_home_override(home_token)


def test_real_plugin_manager_and_dispatch_guard(tmp_path, monkeypatch):
    pytest.importorskip("hermes_cli.plugins")
    home = tmp_path / "hermes-home"
    plugin_dir = home / "plugins" / "plan-mode"
    workspace = tmp_path / "workspace"
    empty_bundled = tmp_path / "empty-bundled"
    workspace.mkdir()
    empty_bundled.mkdir()
    _copy_plugin(plugin_dir)
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - plan-mode\n  load_timeout_seconds: 0\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(empty_bundled))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "0")
    monkeypatch.setenv("TERMINAL_CWD", str(workspace))

    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from gateway.session_context import clear_session_vars, set_session_vars
    from hermes_cli import plugins

    home_token = set_hermes_home_override(str(home))
    session_tokens = None
    try:
        plugins._reset_plugin_managers_for_tests()
        manager = plugins.get_plugin_manager()
        manager.discover_and_load()
        assert manager._plugins["plan-mode"].enabled

        handler = plugins.get_plugin_command_handler("planmode")
        assert handler is not None
        session_tokens = set_session_vars(
            platform="telegram",
            source="telegram",
            session_key="integration-key",
            session_id="integration-session",
            cwd=str(workspace),
        )
        response = handler("on integration proof")
        plans_dir = workspace / ".hermes" / "plans"
        assert str(plans_dir) in response

        block = plugins.get_pre_tool_call_block_message
        assert block("terminal", {"command": "pwd"}, session_id="integration-session")
        assert block("execute_code", {"code": "1+1"}, session_id="integration-session")
        assert block("mcp_linear_update_issue", {}, session_id="integration-session")
        assert block("unknown_future_tool", {}, session_id="integration-session")
        assert block(
            "write_file", {"path": str(workspace / "outside.md"), "content": "x"},
            session_id="integration-session",
        )
        assert block("read_file", {"path": str(workspace / "notes.md")}, session_id="integration-session") is None
        assert block(
            "write_file", {"path": str(plans_dir / "plan.md"), "content": "# Plan"},
            session_id="integration-session",
        ) is None

        # Exercise the exact pre-hook entry and ContextVar propagation helper used
        # by Hermes' sequential/concurrent tool executor paths.
        from agent.tool_executor import _pre_tool_block
        from tools.thread_context import propagate_context_to_thread

        agent = SimpleNamespace(
            session_id="integration-session",
            _current_turn_id="turn-1",
            _current_api_request_id="request-1",
        )
        ref = SimpleNamespace(
            name="terminal", args={"command": "pwd"}, task_id="default",
            call_id="call-1", trace=[],
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(
                propagate_context_to_thread(lambda: _pre_tool_block(agent, ref)[0])
            )
            assert "Plan mode is on" in future.result(timeout=10)
    finally:
        if session_tokens is not None:
            clear_session_vars(session_tokens)
        plugins._reset_plugin_managers_for_tests()
        reset_hermes_home_override(home_token)


def test_real_tui_plugin_command_cannot_fail_open_across_turn_binding(tmp_path, monkeypatch):
    pytest.importorskip("hermes_cli.plugins")
    home = tmp_path / "hermes-home"
    plugin_dir = home / "plugins" / "plan-mode"
    workspace = tmp_path / "workspace"
    empty_bundled = tmp_path / "empty-bundled"
    workspace.mkdir()
    empty_bundled.mkdir()
    _copy_plugin(plugin_dir)
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - plan-mode\n  load_timeout_seconds: 0\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(empty_bundled))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "0")
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.delenv("HERMES_SESSION_KEY", raising=False)
    monkeypatch.delenv("HERMES_SESSION_SOURCE", raising=False)
    monkeypatch.setitem(sys.modules, "tui_gateway.server", ModuleType("tui_gateway.server"))

    from gateway.session_context import clear_session_vars, set_session_vars
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_cli import plugins
    from tui_gateway import methods_tools

    home_token = set_hermes_home_override(str(home))
    turn_tokens = None
    try:
        plugins._reset_plugin_managers_for_tests()
        manager = plugins.get_plugin_manager()
        manager.discover_and_load()
        handler = plugins.get_plugin_command_handler("planmode")
        assert handler is not None

        monkeypatch.setattr(methods_tools, "_tools_mod", importlib.import_module, raising=False)
        params = inspect.signature(methods_tools._run_plugin_command).parameters
        if "session" in params:
            monkeypatch.setattr(
                methods_tools,
                "_set_session_context",
                lambda key, cwd=None: set_session_vars(
                    source="tui", session_key=key, session_id=key, cwd=cwd or ""
                ),
                raising=False,
            )
            monkeypatch.setattr(methods_tools, "_clear_session_context", clear_session_vars, raising=False)
            response = methods_tools._run_plugin_command(
                handler,
                "on regression proof",
                {"session_key": "tui-session-key", "cwd": str(workspace)},
            )
        else:
            response = methods_tools._run_plugin_command(handler, "on regression proof")

        runtime_version = tuple(int(part) for part in version("hermes-agent").split(".")[:3])
        if runtime_version < (0, 21, 4):
            assert "refused" in response.lower()
            assert "session binding" in response.lower()
        else:
            assert "Plan mode is on" in response
            turn_tokens = set_session_vars(
                source="tui",
                session_key="tui-session-key",
                session_id="tui-session-key",
                ui_session_id="ui-tab",
                cwd=str(workspace),
            )
            assert plugins.get_pre_tool_call_block_message(
                "terminal", {"command": "pwd"}, session_id="tui-session-key"
            )
    finally:
        if turn_tokens is not None:
            clear_session_vars(turn_tokens)
        plugins._reset_plugin_managers_for_tests()
        reset_hermes_home_override(home_token)


@pytest.mark.xfail(
    strict=True,
    reason="upstream slash.exec resolves the launch-profile plugin manager before profile scoping",
)
def test_real_tui_slash_exec_does_not_leak_profile_plugin_state(tmp_path, monkeypatch):
    pytest.importorskip("hermes_cli.plugins")
    runtime_version = tuple(int(part) for part in version("hermes-agent").split(".")[:3])
    if runtime_version < (0, 21, 4):
        pytest.skip("real profile-aware slash.exec regression targets pinned upstream")
    homes = [tmp_path / "profile-a", tmp_path / "profile-b"]
    workspace = tmp_path / "workspace"
    empty_bundled = tmp_path / "empty-bundled"
    workspace.mkdir()
    empty_bundled.mkdir()
    for home in homes:
        _copy_plugin(home / "plugins" / "plan-mode")
        (home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - plan-mode\n  load_timeout_seconds: 0\n",
            encoding="utf-8",
        )

    monkeypatch.setenv("HERMES_HOME", str(homes[0]))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(empty_bundled))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "0")

    from gateway.session_context import clear_session_vars, set_session_vars
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_cli import plugins
    from tui_gateway import server

    plugins._reset_plugin_managers_for_tests()
    home_token = set_hermes_home_override(str(homes[0]))
    session_tokens = None
    runtime_id = "round2-profile-b-runtime"
    try:
        manager = plugins.get_plugin_manager()
        manager.discover_and_load()
        session_tokens = set_session_vars(
            source="tui",
            session_key="shared-session-key",
            session_id="shared-session-key",
            cwd=str(workspace),
        )
        response = plugins.get_plugin_command_handler("planmode")("on profile A")
        assert "Plan mode is on" in response
        clear_session_vars(session_tokens)
        session_tokens = None

        server._sessions[runtime_id] = {
            "session_key": "shared-session-key",
            "cwd": str(workspace),
            "profile_home": str(homes[1]),
        }
        result = server._methods["slash.exec"](
            "round2-f5",
            {"session_id": runtime_id, "command": "/planmode status"},
        )
        output = result["result"]["output"]

        assert "Plan mode: off" in output
    finally:
        server._sessions.pop(runtime_id, None)
        if session_tokens is not None:
            clear_session_vars(session_tokens)
        plugins._reset_plugin_managers_for_tests()
        reset_hermes_home_override(home_token)


def test_real_session_bound_cwd_wins_over_backend_process_cwd(tmp_path, monkeypatch):
    pytest.importorskip("hermes_cli.plugins")
    home = tmp_path / "hermes-home"
    plugin_dir = home / "plugins" / "plan-mode"
    workspace = tmp_path / "session-workspace"
    backend_cwd = tmp_path / "backend-cwd"
    empty_bundled = tmp_path / "empty-bundled"
    workspace.mkdir()
    backend_cwd.mkdir()
    empty_bundled.mkdir()
    _copy_plugin(plugin_dir)
    (home / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - plan-mode\n  load_timeout_seconds: 0\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(empty_bundled))
    monkeypatch.setenv("HERMES_ENABLE_PROJECT_PLUGINS", "0")
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    monkeypatch.chdir(backend_cwd)

    from gateway.session_context import clear_session_vars, set_session_vars
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from hermes_cli import plugins

    home_token = set_hermes_home_override(str(home))
    session_tokens = set_session_vars(
        source="tui",
        session_key="cwd-session-key",
        session_id="cwd-session-key",
        cwd=str(workspace),
    )
    try:
        plugins._reset_plugin_managers_for_tests()
        manager = plugins.get_plugin_manager()
        manager.discover_and_load()
        handler = plugins.get_plugin_command_handler("planmode")
        assert handler is not None

        response = handler("on cwd proof")

        assert "Plan mode is on" in response
        assert str(workspace / ".hermes" / "plans") in response
        assert str(backend_cwd / ".hermes" / "plans") not in response
    finally:
        clear_session_vars(session_tokens)
        plugins._reset_plugin_managers_for_tests()
        reset_hermes_home_override(home_token)
