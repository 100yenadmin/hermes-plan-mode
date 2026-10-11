"""Reply-to-file path: public host seams, isolated homes, no model calls."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from plan_mode import plugin as pm
from test_plan_mode import FakeContext, session_env


TEXT = "# Parser plan\n\nChange parser.py and its caller cli.py. Add a behaviour test; run pytest tests/test_parser.py."


def message(text=TEXT, *, codex=True, ids=("submit",)):
    items = [{"type": "message", "role": "assistant", "phase": phase,
              "content": [{"type": "output_text", "text": value}]}
             for phase, value in [("analysis", "PRIVATE REASONING"), ("commentary", text)]]
    return SimpleNamespace(content="" if codex else text,
                           codex_message_items=items if codex else None,
                           tool_calls=[SimpleNamespace(id=call_id) for call_id in ids])


@pytest.fixture
def once(session_env, tmp_path, monkeypatch):
    monkeypatch.delenv("PLAN_MODE_LEVERS", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    session_env.update(TERMINAL_CWD=str(tmp_path), HERMES_UI_SESSION_ID="",
                       HERMES_SESSION_PLATFORM="cli")  # serve really reports cli
    monkeypatch.setattr(pm, "_session_context_is_engaged", lambda: True)
    config = {"display": {"interim_assistant_messages": True, "show_commentary": True},
              "model": {"provider": "openai-codex"}}
    from hermes_cli import config_effective
    monkeypatch.setattr(config_effective, "load_user_config_effective", lambda *a, **k: config)
    monkeypatch.setitem(sys.modules, "tui_gateway.server", SimpleNamespace(
        _active_config_path=lambda: tmp_path / "home/config.yaml"))
    ctx = FakeContext()
    ctx.settings["levers"] = "plan_once"
    plugin = pm.PlanModePlugin(ctx)
    plugin.register()
    assert "Plan mode is on" in plugin.command("on parser")
    session_env["HERMES_UI_SESSION_ID"] = "ui-1"
    return plugin, config


def capture(plugin, text=TEXT, *, codex=True, ids=("submit",)):
    plugin.ctx.hooks["post_api_request"](
        assistant_message=message(text, codex=codex, ids=ids),
        provider="openai-codex" if codex else "anthropic", session_id="s")


def submit(plugin, call_id="submit", **args):
    return plugin.pre_tool_call("plan_mode", {"action": "submit", **args},
                                session_id="s", tool_call_id=call_id)


@pytest.mark.parametrize("surface,ui", [("tui", True), ("desktop", True), ("cli", True),
                                        ("serve", True), ("cli", False),
                                        ("telegram", False), ("mattermost", False)])
@pytest.mark.parametrize("interim,commentary", [(True, True), (False, True), (True, False)])
def test_once_eligibility_matrix(once, session_env, surface, ui, interim, commentary):
    plugin, config = once
    session_env.update(HERMES_SESSION_PLATFORM=surface, HERMES_UI_SESSION_ID="ui-1" if ui else "")
    config["display"].update(interim_assistant_messages=interim, show_commentary=commentary)
    note = plugin.pre_llm_call()["context"]
    assert ("Write the complete plan as your reply text" in note) == (ui and interim and commentary)
    if ui and interim and commentary:
        assert 'plan_mode(action="submit") with no path' in note
        assert "do not write the plan file yourself" in note
    else:
        assert "as Markdown to" in note


@pytest.mark.parametrize("signal", ["module", "bound-context"])
def test_once_requires_gateway_signal(once, monkeypatch, signal):
    plugin, _ = once
    if signal == "module":
        monkeypatch.delitem(sys.modules, "tui_gateway.server")
    else:
        monkeypatch.setattr(pm, "_session_context_is_engaged", lambda: False)
    assert "Write the complete plan as your reply text" not in plugin.pre_llm_call()["context"]


def test_once_unreadable_config_falls_back(once, monkeypatch):
    plugin, _ = once
    from hermes_cli import config_effective
    def unreadable(*a, **k):
        raise OSError("unreadable test config")
    monkeypatch.setattr(config_effective, "load_user_config_effective", unreadable)
    assert "as Markdown to" in plugin.pre_llm_call()["context"]
    capture(plugin)
    assert submit(plugin)["action"] == "block"


@pytest.mark.parametrize("codex", [True, False])
def test_once_capture_writes_exact_visible_bytes_and_approves(once, codex):
    plugin, _ = once
    capture(plugin, "<think>PRIVATE</think>\n" + TEXT, codex=codex)
    gate = submit(plugin)
    assert gate["action"] == "approve"
    state = plugin._load_state("ui:ui-1")
    path = Path(state["submission"]["path"])
    assert path.read_bytes() in (TEXT.encode(), (TEXT + "\n").encode())
    assert str(path) in state["plan_files"]
    assert state["submission"]["digest"] == hashlib.sha256(path.read_bytes()).hexdigest()
    plugin.ctx.hooks["post_approval_response"](
        pattern_key="plugin_rule:" + gate["rule_key"], choice="once", tool_call_id="submit")
    assert json.loads(plugin.tool({"action": "submit"}))["approved"] is True


@pytest.mark.parametrize("text", [None, "short", "x" * (65536 + 1), "é" * 40000],
                         ids=["missing", "short", "oversized", "oversized-utf8"])
def test_once_capture_fallback_bounds(once, text):
    plugin, _ = once
    if text is not None:
        capture(plugin, text)
    gate = submit(plugin)
    assert gate["action"] == "block"
    assert "absolute" in gate["message"] and 'path' in gate["message"]
    assert not plugin._load_state("ui:ui-1")["plan_files"]


def test_once_interleaved_sessions_and_call_ids(once, session_env):
    plugin, _ = once
    capture(plugin, TEXT, ids=("a", "other"))
    session_env.update(HERMES_SESSION_KEY="second", HERMES_UI_SESSION_ID="ui-2")
    plugin.command("on second")
    capture(plugin, TEXT + "\nSecond session", ids=("a",))
    assert submit(plugin, "other")["action"] == "block"
    assert submit(plugin, "a")["action"] == "approve"
    second = Path(plugin._load_state("ui:ui-2")["submission"]["path"])
    assert "Second session" in second.read_text()
    session_env.update(HERMES_SESSION_KEY="unit-session", HERMES_UI_SESSION_ID="ui-1")
    assert submit(plugin, "a")["action"] == "approve"
    first = Path(plugin._load_state("ui:ui-1")["submission"]["path"])
    assert first != second and first.read_text().strip() == TEXT


def test_once_codex_authoritative_call_id(once):
    plugin, _ = once
    response = message()
    response.tool_calls = [{"id": "item-id", "call_id": "submit|item-id"}]
    plugin.ctx.hooks["post_api_request"](assistant_message=response, provider="openai-codex")
    assert submit(plugin)["action"] == "approve"


def test_once_bounded_capture(once):
    plugin, _ = once
    capture(plugin, ids=tuple(f"id-{i}" for i in range(257)))
    assert submit(plugin, "id-0")["action"] == "block"
    assert submit(plugin, "id-256")["action"] == "approve"


def test_once_rechecks_visibility_at_submit(once):
    plugin, config = once
    capture(plugin)
    config["display"]["show_commentary"] = False
    assert submit(plugin)["action"] == "block"
    assert not plugin._load_state("ui:ui-1")["plan_files"]


def test_once_plain_provider_does_not_require_commentary(once):
    plugin, config = once
    config["model"]["provider"] = "anthropic"
    config["display"]["show_commentary"] = False
    assert "Write the complete plan as your reply text" in plugin.pre_llm_call()["context"]
    capture(plugin, codex=False)
    assert submit(plugin)["action"] == "approve"


def test_once_refuses_symlinked_root(once, tmp_path):
    plugin, _ = once
    capture(plugin)
    plans = tmp_path / ".hermes/plans"
    plans.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    plans.symlink_to(outside, target_is_directory=True)
    assert submit(plugin)["action"] == "block"
    assert not list(outside.iterdir())


def test_once_explicit_path_unchanged(once):
    plugin, _ = once
    plugin.pre_llm_call()
    state = plugin._load_state("ui:ui-1")
    path = Path(state["plans_dir"]) / "explicit.md"
    assert plugin.pre_tool_call("write_file", {"path": str(path)}) is None
    path.write_text(TEXT)
    capture(plugin, TEXT + "Different reply")
    assert submit(plugin, path=str(path))["action"] == "approve"
    assert plugin._load_state("ui:ui-1")["submission"]["path"] == str(path)
    assert path.read_text() == TEXT


def test_once_denial_writes_new_revision(once):
    plugin, _ = once
    capture(plugin)
    assert submit(plugin)["action"] == "approve"
    first = plugin._load_state("ui:ui-1")["submission"]["path"]
    plugin.command("reject make it smaller")
    capture(plugin, TEXT + "\nRevised", ids=("revision",))
    assert submit(plugin, "revision")["action"] == "approve"
    state = plugin._load_state("ui:ui-1")
    assert state["submission"]["path"] != first and state["revision"] == 2
    assert Path(first).read_text().strip() == TEXT


def test_once_prior_activation_capture_not_reused(once):
    plugin, _ = once
    capture(plugin)
    plugin.command("off")
    plugin.command("on new task")
    assert submit(plugin)["action"] == "block"
