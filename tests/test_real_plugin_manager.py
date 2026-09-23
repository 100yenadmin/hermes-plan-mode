from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import version
import inspect
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

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
