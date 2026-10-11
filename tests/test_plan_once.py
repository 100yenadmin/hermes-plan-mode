"""Reply-to-file path: public host seams, isolated homes, no model calls."""
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from plan_mode import plugin as pm
from test_plan_mode import FakeContext, plan_profile, session_env


TEXT = "# Parser plan\n\nChange parser.py and its caller cli.py. Add a behaviour test; run pytest tests/test_parser.py."


SAVED_NOTE = ("The plan was saved for you from your reply; saving it is done. Your task now is to "
              "implement it in this turn.")
SIGN_OFF = "I'm Hermes, built by Nous Research. `/help` shows available commands."


def reply(text):
    return f"<proposed_plan>\n{text}\n</proposed_plan>"


def message(text=reply(TEXT), *, codex=True, ids=("submit",)):
    items = [{"type": "message", "role": "assistant", "phase": phase,
              "content": [{"type": "output_text", "text": value}]}
             for phase, value in [("analysis", "PRIVATE REASONING"), ("commentary", text)]]
    return SimpleNamespace(content="" if codex else text,
                           codex_message_items=items if codex else None,
                           tool_calls=[SimpleNamespace(id=call_id) for call_id in ids])


@pytest.fixture
def once(session_env, tmp_path, monkeypatch, plan_profile):
    pytest.importorskip("hermes_cli.config_effective")
    pytest.importorskip("agent.history_commentary", reason="Hermes build lacks the commentary helpers plan_once needs")
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
    ctx.settings.update(levers="plan_once", plan_profile=plan_profile)
    plugin = pm.PlanModePlugin(ctx)
    plugin.register()
    assert "Plan mode is on" in plugin.command("on parser")
    session_env["HERMES_UI_SESSION_ID"] = "ui-1"
    return plugin, config


def capture(plugin, text=TEXT, *, codex=True, ids=("submit",), raw=False):
    plugin.ctx.hooks["post_api_request"](
        assistant_message=message(text if raw else reply(text), codex=codex, ids=ids),
        provider="openai-codex" if codex else "anthropic", session_id="s")


def submit(plugin, call_id="submit", **args):
    return plugin.pre_tool_call("plan_mode", {"action": "submit", **args},
                                session_id="s", tool_call_id=call_id)


@pytest.fixture
def once_config_file(once, monkeypatch):
    plugin, config = once
    from hermes_cli import config_effective
    path = sys.modules["tui_gateway.server"]._active_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))
    monkeypatch.setattr(config_effective, "load_user_config_effective",
                        lambda path, **kw: json.loads(Path(path).read_text()))
    return plugin, config, path


def test_once_commentary_enable_keeps_session_snapshot(once_config_file):
    plugin, config, path = once_config_file
    config["display"]["show_commentary"] = False
    path.write_text(json.dumps(config))
    assert "as Markdown to" in plugin.pre_llm_call()["context"]
    config["display"]["show_commentary"] = True
    path.write_text(json.dumps(config))
    assert "as Markdown to" in plugin.pre_llm_call()["context"]
    capture(plugin)
    assert submit(plugin)["action"] == "block"
    assert not plugin._load_state("ui:ui-1")["plan_files"]


@pytest.mark.parametrize("flag", ["interim_assistant_messages", "show_commentary"])
@pytest.mark.parametrize("initial,current", [(True, False), (True, True), (False, True), (False, False)])
def test_once_visibility_requires_initial_and_current_flags(once_config_file, flag, initial, current):
    _, config, path = once_config_file
    config["display"][flag] = initial
    path.write_text(json.dumps(config))
    assert pm._plan_once_visible() is initial
    config["display"][flag] = current
    path.write_text(json.dumps(config))
    assert pm._plan_once_visible() is (initial and current)


def test_once_sessions_keep_independent_display_snapshots(once, session_env):
    _, config = once
    config["display"]["show_commentary"] = False
    assert pm._plan_once_visible() is False
    session_env["HERMES_UI_SESSION_ID"] = "ui-2"
    config["display"]["show_commentary"] = True
    assert pm._plan_once_visible() is True
    session_env["HERMES_UI_SESSION_ID"] = "ui-1"
    assert pm._plan_once_visible() is False
    assert pm._PLAN_ONCE_DISPLAY_SNAPSHOTS == {"ui-1": (True, False), "ui-2": (True, True)}


def test_once_display_snapshots_evict_oldest_after_256(once, session_env):
    _, config = once
    session_env["HERMES_UI_SESSION_ID"] = "ui-0"
    config["display"]["show_commentary"] = False
    assert pm._plan_once_visible() is False
    config["display"]["show_commentary"] = True
    for index in range(1, 256):
        session_env["HERMES_UI_SESSION_ID"] = f"ui-{index}"
        assert pm._plan_once_visible() is True
    assert len(pm._PLAN_ONCE_DISPLAY_SNAPSHOTS) == 256
    session_env["HERMES_UI_SESSION_ID"] = "ui-0"
    assert pm._plan_once_visible() is False  # reads must not refresh insertion order
    session_env["HERMES_UI_SESSION_ID"] = "ui-256"
    assert pm._plan_once_visible() is True
    assert list(pm._PLAN_ONCE_DISPLAY_SNAPSHOTS) == [f"ui-{index}" for index in range(1, 257)]
    session_env["HERMES_UI_SESSION_ID"] = "ui-0"
    assert pm._plan_once_visible() is True  # the evicted session takes a fresh snapshot
    assert len(pm._PLAN_ONCE_DISPLAY_SNAPSHOTS) == 256
    assert "ui-1" not in pm._PLAN_ONCE_DISPLAY_SNAPSHOTS


@pytest.mark.parametrize("failure", ["guard", "loader", "display", "helpers", "server",
                                     "context", "reader-missing", "reader-error", "session-id"])
def test_once_failed_visibility_check_does_not_snapshot(once, monkeypatch, session_env, failure):
    _, config = once
    from hermes_cli import config as host_config, config_effective
    config["display"]["show_commentary"] = False

    def unreadable(*args, **kwargs):
        raise OSError("unreadable test config or context")

    with monkeypatch.context() as patch:
        if failure == "guard":
            patch.setattr(host_config, "require_readable_config_before_write", unreadable)
        elif failure == "loader":
            patch.setattr(config_effective, "load_user_config_effective", unreadable)
        elif failure == "display":
            patch.setitem(config, "display", [])
        elif failure == "helpers":
            patch.setitem(sys.modules, "agent.history_commentary", None)
        elif failure == "server":
            patch.delitem(sys.modules, "tui_gateway.server")
        elif failure == "context":
            patch.setattr(pm, "_session_context_is_engaged", lambda: False)
        elif failure == "reader-missing":
            patch.setattr(pm, "_session_reader", lambda: None)
        elif failure == "reader-error":
            patch.setattr(pm, "_session_reader", lambda: unreadable)
        else:
            patch.setitem(session_env, "HERMES_UI_SESSION_ID", "")
        assert pm._plan_once_visible() is False
        assert pm._PLAN_ONCE_DISPLAY_SNAPSHOTS == {}
    config["display"]["show_commentary"] = True
    assert pm._plan_once_visible() is True
    assert pm._PLAN_ONCE_DISPLAY_SNAPSHOTS == {"ui-1": (True, True)}


def test_once_snapshot_keeps_raw_flags_for_each_provider(once):
    _, config = once
    config["model"]["provider"] = "anthropic"
    config["display"]["show_commentary"] = False
    assert pm._plan_once_visible() is True
    assert pm._PLAN_ONCE_DISPLAY_SNAPSHOTS == {"ui-1": (True, False)}
    config["display"]["show_commentary"] = True
    assert pm._plan_once_visible(provider="openai-codex") is False
    assert pm._plan_once_visible(provider="anthropic", codex=True) is False
    config["model"]["provider"] = ""
    assert pm._plan_once_visible() is False
    assert pm._plan_once_visible(provider="anthropic") is True


@pytest.mark.parametrize("value,expected", [("false", False), ("on", True), (None, False), (0, False)])
def test_once_snapshot_preserves_flag_coercion(once, value, expected):
    _, config = once
    config["display"] = {"interim_assistant_messages": value, "show_commentary": value}
    assert pm._plan_once_visible() is expected
    assert pm._PLAN_ONCE_DISPLAY_SNAPSHOTS == {"ui-1": (expected, expected)}


def test_once_all_consumers_honor_current_visibility(once_config_file):
    plugin, config, path = once_config_file
    capture(plugin)
    config["display"]["show_commentary"] = False
    path.write_text(json.dumps(config))
    assert "as Markdown to" in plugin.pre_llm_call()["context"]
    state = plugin._load_state("ui:ui-1")
    assert plugin._save_reply_plan("ui:ui-1", state, "submit")["action"] == "block"
    capture(plugin, ids=("hidden",))
    assert ("ui:ui-1", "hidden") not in plugin._reply_captures
    assert submit(plugin)["action"] == "block"
    assert not plugin._load_state("ui:ui-1")["plan_files"]


@pytest.mark.parametrize("surface,ui", [("tui", True), ("desktop", True), ("cli", True),
                                        ("serve", True), ("cli", False),
                                        ("telegram", False), ("mattermost", False)])
@pytest.mark.parametrize("interim,commentary", [(True, True), (False, True), (True, False)])
def test_once_eligibility_matrix(once, session_env, surface, ui, interim, commentary):
    plugin, config = once
    session_env.update(HERMES_SESSION_PLATFORM=surface, HERMES_UI_SESSION_ID="ui-1" if ui else "")
    config["display"].update(interim_assistant_messages=interim, show_commentary=commentary)
    note = plugin.pre_llm_call()["context"]
    assert ("Write the complete plan in your reply, in Markdown, between a line <proposed_plan>" in note) == (ui and interim and commentary)
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
    assert "Write the complete plan in your reply, in Markdown, between a line <proposed_plan>" not in plugin.pre_llm_call()["context"]


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
    capture(plugin, "<think>PRIVATE</think>\nPreamble\n" + reply("\n" + TEXT + "\n \n") + "\n" + SIGN_OFF, codex=codex, raw=True)
    gate = submit(plugin)
    assert gate["action"] == "approve"
    state = plugin._load_state("ui:ui-1")
    path = Path(state["submission"]["path"])
    assert path.read_bytes() == (TEXT + "\n").encode()
    assert "parser-plan" in path.name
    assert SIGN_OFF not in path.read_text() and "Preamble" not in path.read_text()
    assert str(path) in state["plan_files"]
    assert state["submission"]["digest"] == hashlib.sha256(path.read_bytes()).hexdigest()
    plugin.ctx.hooks["post_approval_response"](
        pattern_key="plugin_rule:" + gate["rule_key"], choice="once", tool_call_id="submit")
    result = json.loads(plugin.tool({"action": "submit"}))
    assert result["approved"] is True
    assert result["message"].startswith(SAVED_NOTE + " ")


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
    assert "Write the complete plan in your reply, in Markdown, between a line <proposed_plan>" in plugin.pre_llm_call()["context"]
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


def test_once_ineligible_without_commentary_helpers(once, monkeypatch):
    """Hermes before v2026.9.24 has no agent.history_commentary: keep the file-writing note."""
    plugin, _ = once
    assert "Write the complete plan in your reply" in plugin.pre_llm_call()["context"]
    monkeypatch.setitem(sys.modules, "agent.history_commentary", None)
    note = plugin.pre_llm_call()["context"]
    assert "Write the complete plan in your reply" not in note
    assert 'plan_mode(action="submit") with no path' not in note


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_once_ignores_fenced_tag_lines(once, fence):
    plugin, _ = once
    inner = TEXT + f"\n{fence}text\n<proposed_plan>\nexample\n</proposed_plan>\n{fence}"
    capture(plugin, f"{fence}\n<proposed_plan>\nignored\n</proposed_plan>\n{fence}\n" + reply(inner), raw=True)
    assert submit(plugin)["action"] == "approve"
    path = Path(plugin._load_state("ui:ui-1")["submission"]["path"])
    assert path.read_bytes() == (inner + "\n").encode()


@pytest.mark.parametrize("text,reason", [
    (TEXT, "no"),
    (reply(TEXT) + "\n" + reply(TEXT), "more than one"),
    ("<proposed_plan>\n" + TEXT, "unbalanced"),
    ("</proposed_plan>\n" + TEXT + "\n<proposed_plan>", "order"),
    (reply("\n \n"), "empty"),
    (reply("short"), "80"),
    (reply("x" * 65537), "64 KiB"),
    (reply("é" * 40000), "64 KiB"),
    ("Preamble\n    <proposed_plan>\n" + TEXT + "\n    </proposed_plan>\nSign-off", "no"),
    ("prefix <proposed_plan>\n" + TEXT + "\n</proposed_plan>", "unbalanced"),
], ids=["missing-tags", "two-pairs", "unbalanced", "reversed", "empty", "short",
        "oversized", "oversized-utf8", "indented-code", "inline-tag"])
def test_once_invalid_blocks_explain_fallback(once, text, reason):
    plugin, _ = once
    capture(plugin, text, raw=True)
    result = submit(plugin)
    assert result["action"] == "block"
    assert reason in result["message"]
    assert "wrap the plan in one <proposed_plan>" in result["message"]
    assert "absolute" in result["message"] and "path" in result["message"]
    assert not plugin._load_state("ui:ui-1")["plan_files"]


@pytest.mark.parametrize("indent", ["", " ", "   "])
def test_once_inner_cap_and_tag_indentation(once, indent):
    plugin, _ = once
    inner = "# Boundary plan\n" + "x" * (65536 - len("# Boundary plan\n"))
    capture(plugin, "preamble" * 10000 + f"\n{indent}<proposed_plan>\n{inner}\n{indent}</proposed_plan>\n" + SIGN_OFF,
            raw=True)
    assert submit(plugin)["action"] == "approve"
    path = Path(plugin._load_state("ui:ui-1")["submission"]["path"])
    assert path.read_bytes() == (inner + "\n").encode()


@pytest.mark.parametrize("route", ["gate", "clarify", "typed", "typed-open"])
@pytest.mark.parametrize("plugin_saved", [True, False])
def test_once_saved_approval_note_routes(once, session_env, route, plugin_saved):
    plugin, _ = once
    if plugin_saved:
        capture(plugin)
        gate = submit(plugin)
    else:
        plugin.pre_llm_call()
        path = Path(plugin._load_state("ui:ui-1")["plans_dir"]) / "explicit.md"
        assert plugin.pre_tool_call("write_file", {"path": str(path)}) is None
        path.write_text(TEXT)
        gate = submit(plugin, path=str(path))
    assert gate["action"] == "approve"
    state = plugin._load_state("ui:ui-1")
    path = state["submission"]["path"]
    if route == "gate":
        plugin.ctx.hooks["post_approval_response"](
            pattern_key="plugin_rule:" + gate["rule_key"], choice="once", tool_call_id="submit")
        message = json.loads(plugin.tool({"action": "submit"}))["message"]
        expected = f"The user approved plan rev 1 at {path}. Plan mode is off. Implement it now: {plugin._execute_hint()}"
    elif route == "clarify":
        clarify = json.loads(plugin.tool({"action": "submit"}))["clarify"]
        args = {"questions": [clarify]}
        assert plugin.pre_tool_call("clarify", args, tool_call_id="clarify") is None
        payload = json.dumps({"question": clarify["question"], "user_response": clarify["choices"][0]})
        plugin.post_tool_call("clarify", args, tool_call_id="clarify", status="ok", result=payload)
        transformed = plugin.transform_tool_result("clarify", args, tool_call_id="clarify", result=payload)
        message = json.loads(transformed)["plan_mode_approval"] if transformed else ""
        expected = f"The user approved plan rev 1. Implement it now: {plugin._execute_hint()}"
        if not plugin_saved:
            expected = ""  # plan_once alone never transformed explicit-file clarify results
    else:
        if route == "typed":
            plugin.tool({"action": "submit"})  # no open card, so the queued note is injected
        session_env["HERMES_UI_SESSION_ID"] = ""  # command-only alias
        plugin.command("approve")
        alias = plugin._load_state("sk:unit-session")
        assert alias["submission"].get("plugin_saved", False) is plugin_saved
        session_env["HERMES_UI_SESSION_ID"] = "ui-1"
        if route == "typed-open":
            message = json.loads(plugin.tool({"action": "submit"}))["message"]
            expected = (f"The user approved plan rev 1 at {path} with /planmode approve. "
                        f"Plan mode is off. Implement it now: {plugin._execute_hint()}")
        else:
            message = plugin._load_state("ui:ui-1")["pending_note"]
            expected = f"The user approved the plan at {path}. Implement it now." + plugin._commit_rule()
    assert message == (SAVED_NOTE + " " if plugin_saved else "") + expected


def test_once_planning_note_delimiters(once):
    plugin, _ = once
    note = plugin.pre_llm_call()["context"]
    assert "<proposed_plan>" in note and "</proposed_plan>" in note
    assert "between a line <proposed_plan> and a line </proposed_plan>" in note and "outside" in note
    assert "saves only the text between the tags" in note


def test_po2_note_puts_tags_in_step_two_with_blank_lines(once):
    plugin, _ = once
    note = plugin.pre_llm_call()["context"]
    step2 = note[note.index("2. "):note.index("\n3. ")]
    assert "between a line <proposed_plan> and a line </proposed_plan>" in step2
    assert "blank line after the opening tag and before the closing tag" in step2
    assert "outside the tags" in step2
    assert "saves only the text between the tags" in note[note.index("\n3. "):]


def test_po2_tag_lines_tolerate_trailing_spaces():
    text, error = pm.PlanModePlugin._reply_plan("Intro\n<proposed_plan>  \n\n" + "x" * 90 + "\n\n</proposed_plan>\t\nBye")
    assert error == "" and text == "x" * 90 + "\n"


@pytest.fixture
def tui_default(session_env, tmp_path, monkeypatch):
    """A lean-default plugin (no levers setting) with the tui_gateway seams present."""
    monkeypatch.delenv("PLAN_MODE_LEVERS", raising=False)
    monkeypatch.setattr(pm, "_session_context_is_engaged", lambda: True)
    monkeypatch.setitem(sys.modules, "tui_gateway.server", SimpleNamespace(
        _active_config_path=lambda: tmp_path / "home/config.yaml"))
    session_env.update(TERMINAL_CWD=str(tmp_path), HERMES_UI_SESSION_ID="ui-1", HERMES_SESSION_PLATFORM="cli")
    return session_env


@pytest.mark.parametrize("seam", ["none", "no_module", "no_ui_id", "not_engaged"])
def test_default_lean_adds_plan_once_only_on_tui_sessions(tui_default, monkeypatch, seam):
    if seam == "no_module":
        monkeypatch.delitem(sys.modules, "tui_gateway.server")
    elif seam == "no_ui_id":
        tui_default["HERMES_UI_SESSION_ID"] = ""
    elif seam == "not_engaged":
        monkeypatch.setattr(pm, "_session_context_is_engaged", lambda: False)
    levers = pm.PlanModePlugin(FakeContext())._levers()
    assert levers == set(pm.LEAN_LEVERS) | ({"plan_once"} if seam == "none" else set())


@pytest.mark.parametrize("settings", [{"plan_profile": "classic"}, {"plan_style": "core"},
                                      {"plan_skill": "my-plan"}, {"levers": "lean_craft,todo_batch"}])
def test_default_plan_once_needs_the_lean_default(tui_default, monkeypatch, settings):
    monkeypatch.setattr(pm, "_skill_view_block_reason", lambda: None)  # the plan skill is loadable
    ctx = FakeContext()
    ctx.settings.update(settings)
    assert "plan_once" not in pm.PlanModePlugin(ctx)._levers()


def test_default_lean_on_tui_asks_for_the_reply_plan(tui_default, monkeypatch, tmp_path):
    pytest.importorskip("hermes_cli.config_effective")
    pytest.importorskip("agent.history_commentary", reason="Hermes build lacks the commentary helpers plan_once needs")
    from hermes_cli import config_effective
    monkeypatch.setattr(config_effective, "load_user_config_effective", lambda *a, **k: {
        "display": {"interim_assistant_messages": True, "show_commentary": True}, "model": {"provider": "openai-codex"}})
    plugin = pm.PlanModePlugin(FakeContext())
    plans = tmp_path / ".hermes" / "plans"
    plans.mkdir(parents=True)
    assert "<proposed_plan>" in plugin._planning_note({"plans_dir": str(plans)})
    tui_default["HERMES_UI_SESSION_ID"] = ""
    assert "<proposed_plan>" not in plugin._planning_note({"plans_dir": str(plans)})


@pytest.fixture
def lean_tui(session_env, tmp_path, monkeypatch):
    """The lean default on a tui_gateway session, plan mode on, with plan_once captured from the config."""
    pytest.importorskip("hermes_cli.config_effective")
    pytest.importorskip("agent.history_commentary", reason="Hermes build lacks the commentary helpers plan_once needs")
    monkeypatch.delenv("PLAN_MODE_LEVERS", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    session_env.update(TERMINAL_CWD=str(tmp_path), HERMES_UI_SESSION_ID="", HERMES_SESSION_PLATFORM="cli")
    monkeypatch.setattr(pm, "_session_context_is_engaged", lambda: True)
    from hermes_cli import config_effective
    monkeypatch.setattr(config_effective, "load_user_config_effective", lambda *a, **k: {
        "display": {"interim_assistant_messages": True, "show_commentary": True}, "model": {"provider": "openai-codex"}})
    monkeypatch.setitem(sys.modules, "tui_gateway.server", SimpleNamespace(
        _active_config_path=lambda: tmp_path / "home/config.yaml"))
    ctx = FakeContext()
    ctx.settings.update(plan_style="compact")
    plugin = pm.PlanModePlugin(ctx)
    plugin.register()
    assert "Plan mode is on" in plugin.command("on parser")
    session_env["HERMES_UI_SESSION_ID"] = "ui-1"
    return plugin


@pytest.mark.parametrize("route", ["typed", "typed-open", "gate"])
def test_default_saved_note_on_every_approval_route(lean_tui, session_env, route):
    # Hermes runs typed plugin commands with HERMES_UI_SESSION_ID="" (tui_gateway methods_tools), so the
    # approval must not re-derive the default lever set; the submission records what was saved.
    plugin = lean_tui
    assert "plan_once" in plugin._levers()
    plugin.pre_llm_call()
    capture(plugin)
    gate = submit(plugin)
    assert gate["action"] == "approve", gate
    assert plugin._load_state("ui:ui-1")["submission"].get("plugin_saved") is True
    if route == "gate":
        plugin.ctx.hooks["post_approval_response"](
            pattern_key="plugin_rule:" + gate["rule_key"], choice="once", tool_call_id="submit")
        message = json.loads(plugin.tool({"action": "submit"}))["message"]
    else:
        if route == "typed":
            plugin.tool({"action": "submit"})  # the card is no longer open
        session_env["HERMES_UI_SESSION_ID"] = ""
        assert "plan_once" not in plugin._levers()
        plugin.command("approve")
        session_env["HERMES_UI_SESSION_ID"] = "ui-1"
        if route == "typed-open":
            message = json.loads(plugin.tool({"action": "submit"}))["message"]
        else:
            message = plugin._load_state("ui:ui-1")["pending_note"]
    assert message.startswith(SAVED_NOTE), (route, message)
