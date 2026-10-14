"""Classic CLI write-once: fresh-file receipts and fail-closed display checks."""
import builtins
import copy
from datetime import datetime
import hashlib
import itertools
import json
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace

import pytest

from plan_mode import plugin as pm
from plan_mode import render
from test_approval_card import answer_card, state
from test_levers import IDS, make_plugin
from test_plan_mode import session_env


class CLI:
    def _on_tool_start(self, *args):
        pass

    def _on_tool_complete(self, *args):
        pass

    def __init__(self):
        self.agent = SimpleNamespace(tool_start_callback=self._on_tool_start,
                                     tool_complete_callback=self._on_tool_complete)


class OtherCLI:
    def _on_tool_complete(self, *args):
        pass


@pytest.fixture
def wo(make_plugin, session_env, monkeypatch):
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    monkeypatch.setenv("TERMINAL_ENV", "local")
    # Unit cases pin the cap; the renderer cross-check below imports the real module.
    display = ModuleType("agent.display")
    display._MAX_INLINE_DIFF_LINES = 80
    monkeypatch.setitem(sys.modules, "agent.display", display)
    monkeypatch.setattr(pm, "_edit_review_available", lambda: False)
    monkeypatch.setattr(pm.secrets, "token_hex", lambda size: "fixednonce")
    monkeypatch.setattr(pm, "make_rule_key", lambda *args: "plan-mode:fixed")
    plugin = make_plugin("write_once approval_card")
    plugin.ctx._manager = SimpleNamespace(_cli_ref=CLI())
    plugin.command("on write once")
    return plugin


def _digest(path):
    try:
        return pm.plan_digest(str(path))[0]
    except Exception:
        return ""


def path_for(plugin, name="plan.md"):
    return Path(state(plugin)["plans_dir"]) / name


def submit(plugin, path):
    gate = plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
    assert gate is None or gate["action"] == "approve"
    return json.loads(plugin.tool({"action": "submit"}))


def start_write(plugin, path, call="write", tool="write_file"):
    args = {"path": str(path), "content": "# Plan\n"}
    assert plugin.pre_tool_call(tool, args, **{**IDS, "tool_call_id": call}) is None
    return args


def finish_write(plugin, args, text, call="write", tool="write_file", status="ok"):
    Path(args["path"]).write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    plugin.post_tool_call(tool, args, status=status, **{**IDS, "tool_call_id": call})


def write(plugin, text="# Plan\n", name="plan.md", **kwargs):
    path = path_for(plugin, name)
    args = start_write(plugin, path, **kwargs)
    finish_write(plugin, args, text, **kwargs)
    return path


def complete_without_display(plugin, before, path):
    """Today's exact message, with only the new lever disabled and identical state/nonce."""
    saved = plugin.ctx.settings["levers"]
    plugin.ctx.settings["levers"] = saved.replace("write_once", "").strip() or "none"
    plugin._card_baselines.clear()
    plugin._save_state("sk:unit-session", copy.deepcopy(before))
    result = submit(plugin, path)
    plugin.ctx.settings["levers"] = saved
    return result


def test_fresh_78_line_plan_is_shown_in_full(wo):
    path = write(wo, "".join(f"step {i}\n" for i in range(78)))
    before = copy.deepcopy(state(wo))
    result = submit(wo, path)
    assert state(wo)["submission"]["shown_in_full"] is True
    assert "already seen the complete plan file in the terminal; do not restate it" in result["message"]
    assert "verbatim" not in result["message"]
    expected = complete_without_display(wo, before, path)
    assert result == {**expected, "message": expected["message"].replace(
        "Show the complete plan file verbatim, not a summary. ",
        "The user has already seen the complete plan file in the terminal; do not restate it. ")}


@pytest.mark.parametrize("case", [
    "79-lines", "overwrite", "patch", "twice", "changed-hash", "single-query", "no-cli",
    "no-manager", "callbacks-none", "wrong-complete", "wrong-start", "wrong-instance", "docker",
    "bom", "cr", "esc", "nel", "u2028", "u2029", "plusplus", "empty", "non-utf8",
    "import-failure", "exception", "lever-off", "telegram", "tui", "desktop", "api_server",
    "no-receipt", "missing-cap", "failed-write", "no-call-id",
    *[f"control-{n}" for n in range(32) if n not in (9, 10)],
    *[f"control-{n}" for n in range(127, 160)],
])
def test_fallback_is_byte_identical(wo, session_env, monkeypatch, case):
    text = {"79-lines": "step\n" * 79, "bom": "# Plan\n\ufefftext", "cr": "# Plan\r\n",
            "esc": "# Plan\n\x1b[8mhidden", "nel": "a\x85b", "u2028": "a\u2028b",
            "u2029": "a\u2029b", "plusplus": "# Plan\n++ hidden\n", "empty": "",
            "non-utf8": b"# Plan\n\xff"}.get(case, "# Plan\n")
    if case.startswith("control-"):
        text = "# Plan\na" + chr(int(case.split("-")[1])) + "b"
    path = path_for(wo)
    if case == "overwrite":
        path.write_text("# Old plan\n")
    if case == "no-call-id":
        start_write(wo, path, call="")
        path.write_text(text)
    else:
        args = start_write(wo, path)
        finish_write(wo, args, text, status="failed" if case == "failed-write" else "ok")
        if case == "failed-write":
            # A previously tracked path can still be submitted after a failed rewrite.
            wo._remember_plan_targets("sk:unit-session", state(wo), [str(path)])
    cli = wo.ctx._manager._cli_ref
    if case in {"patch", "twice"}:
        tool = "patch" if case == "patch" else "write_file"
        args = start_write(wo, path, call="second", tool=tool)
        finish_write(wo, args, text, call="second", tool=tool)
    elif case == "changed-hash":
        path.write_text("# Edited on disk\n")
    elif case == "single-query":
        cli._single_query_mode = True
    elif case == "no-cli":
        wo.ctx._manager._cli_ref = None
    elif case == "no-manager":
        del wo.ctx._manager
    elif case == "callbacks-none":
        cli.agent.tool_complete_callback = cli.agent.tool_start_callback = None
    elif case == "wrong-complete":
        cli.agent.tool_complete_callback = OtherCLI()._on_tool_complete
    elif case == "wrong-start":
        cli.agent.tool_start_callback = OtherCLI()._on_tool_complete
    elif case == "wrong-instance":
        cli.agent.tool_complete_callback = CLI()._on_tool_complete
    elif case == "docker":
        monkeypatch.setenv("TERMINAL_ENV", "docker")
    elif case == "import-failure":
        original = builtins.__import__
        def no_display(name, *args, **kwargs):
            if name == "agent.display":
                raise ImportError("display unavailable")
            return original(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", no_display)
    elif case == "missing-cap":
        monkeypatch.delattr(sys.modules["agent.display"], "_MAX_INLINE_DIFF_LINES")
    elif case == "exception":
        class BrokenManager:
            @property
            def _cli_ref(self):
                raise RuntimeError("host probe failed")
        wo.ctx._manager = BrokenManager()
    elif case == "lever-off":
        wo.ctx.settings["levers"] = "approval_card"
    elif case in {"telegram", "desktop", "api_server"}:
        session_env["HERMES_SESSION_PLATFORM"] = case
    elif case == "tui":
        monkeypatch.setattr(pm, "_tui_ui_session_id", lambda: "ui:test")
    elif case == "no-receipt":
        current = state(wo)
        current.pop("fresh_writes", None)
        wo._save_state("sk:unit-session", current)
    before = copy.deepcopy(state(wo))
    result = submit(wo, path)
    assert state(wo)["submission"]["shown_in_full"] is False
    assert result == complete_without_display(wo, before, path)


def test_bypassed_gate_changes_only_display_text(wo):
    wo.ctx.settings["levers"] = "write_once"
    path = write(wo)
    before = copy.deepcopy(state(wo))
    gate = wo.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
    assert gate["action"] == "approve"
    result = json.loads(wo.tool({"action": "submit"}))
    expected = complete_without_display(wo, before, path)
    assert result == {**expected, "message": expected["message"].replace(
        "Make sure the plan is shown, then ask", "The user has already seen the complete plan file in the terminal; do not restate it. Then ask")}


def test_classic_core_prompt_unchanged(wo):
    wo.ctx.settings["levers"] = "write_once"
    path = write(wo)
    before = copy.deepcopy(state(wo))
    gate = wo.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
    wo._ledger.take(gate["rule_key"])
    wo._save_state("sk:unit-session", before)
    wo.ctx.settings["levers"] = ""
    wo.ctx.settings["plan_profile"] = "classic"
    assert wo.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS) == gate


def test_tracking_bounded_and_cleared(wo):
    for i in range(34):
        path = write(wo, name=f"plan-{i}.md", call=f"write-{i}")
    receipts = state(wo)["fresh_writes"]
    assert len(receipts) == 32 and str(path_for(wo, "plan-0.md")) not in receipts
    assert receipts[str(path)] == hashlib.sha256(path.read_bytes()).hexdigest()
    card = submit(wo, path)
    answer_card(wo, card)
    assert not state(wo).get("fresh_writes")
    wo.command("on again")
    write(wo, name="next.md")
    wo.command("off")
    assert not state(wo).get("fresh_writes")


@pytest.mark.parametrize("reverse,second_tool", list(itertools.product([False, True], ["write_file", "patch"])))
def test_pending_collision_never_records_fresh(wo, reverse, second_tool):
    path = path_for(wo)
    first = start_write(wo, path, call="first")
    second = start_write(wo, path, call="second", tool=second_tool)
    calls = [(first, "first", "write_file"), (second, "second", second_tool)]
    for args, call, tool in reversed(calls) if reverse else calls:
        finish_write(wo, args, "# Plan\n", call=call, tool=tool)
    assert str(path) not in state(wo).get("fresh_writes", {})
    assert wo._shown_in_full(state(wo), str(path), _digest(path)) is False


def test_pending_write_cannot_survive_reactivation(wo):
    args = start_write(wo, path_for(wo))
    wo.command("off")
    wo.command("on again")
    finish_write(wo, args, "# Late write\n")
    assert not state(wo).get("fresh_writes")


@pytest.mark.parametrize("backend", ["LOCAL", "", None])
def test_local_env_fallback(wo, monkeypatch, backend):
    if backend is None:
        monkeypatch.delenv("TERMINAL_ENV", raising=False)
    else:
        monkeypatch.setenv("TERMINAL_ENV", backend)
    path = write(wo)
    assert wo._shown_in_full(state(wo), str(path), _digest(path)) is True


@pytest.mark.parametrize("surface", ["cli", "single-query", "none", "telegram", "tui", "desktop", "api_server"])
def test_default_and_explicit_levers(wo, session_env, monkeypatch, surface):
    wo.ctx.settings["levers"] = ""
    if surface == "single-query":
        wo.ctx._manager._cli_ref._single_query_mode = True
    elif surface == "none":
        wo.ctx._manager._cli_ref = None
    elif surface == "tui":
        monkeypatch.setattr(pm, "_tui_ui_session_id", lambda: "ui:test")
    elif surface != "cli":
        session_env["HERMES_SESSION_PLATFORM"] = surface
    assert ("write_once" in wo._levers()) is (surface == "cli")
    wo.ctx.settings["levers"] = "approval_card"
    assert "write_once" not in wo._levers()
    wo.ctx.settings["levers"] = "write_once"
    assert wo._levers() == {"write_once"}
    wo.ctx.settings.update(levers="", plan_profile="classic")
    assert "write_once" not in wo._levers()


def test_note_only_changes_classic_cli(wo):
    note = wo._planning_note(state(wo), platform="cli")
    assert "write_file" in note and "NEW file" in note
    assert "new file name for every revision" in note
    assert "never patch or overwrite" in note
    assert "Do not restate the plan" in note
    assert 'plan_mode(action="submit") on its own' in note
    assert "has not seen the full plan" in note and "verbatim" in note


@pytest.mark.parametrize("platform,classic,enabled,reply,card", list(itertools.product(
    ["cli", "telegram", "tui", "desktop", "api_server"], [False, True], [False, True], [False, True], [False, True])))
def test_other_note_variants_unchanged(platform, classic, enabled, reply, card):
    levers = {"plan_once"} if reply else set()
    kwargs = dict(platform=platform, reply_plan=reply, card_path=card,
                  ui_session=platform == "tui")
    now = datetime(2026, 10, 14, 12, 0, 0)
    before = render.planning_note("/plans", now, levers=levers, **kwargs)
    after = render.planning_note("/plans", now, levers=levers | ({"write_once"} if enabled else set()),
                                 classic_cli=classic, **kwargs)
    if not (platform == "cli" and classic and enabled and not reply):
        assert after == before


@pytest.mark.parametrize("text", ["step\n" * 78, "# Plan\n\n\tindent\n-- ordinary\nend",
                                  "# Plan\n" + "long " * 2000,
                                  "step\n" * 79, "# Plan\n++ hidden header\nend\n"],
                         ids=["78-lines", "whitespace", "long-line", "79-lines", "fake-header"])
def test_real_renderer_cross_check(make_plugin, session_env, monkeypatch, text):
    display = pytest.importorskip("agent.display")
    if not hasattr(display, "capture_local_edit_snapshot"):
        pytest.skip("Host has no inline diff renderer")
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    monkeypatch.setenv("TERMINAL_ENV", "local")
    plugin = make_plugin("write_once approval_card")
    plugin.ctx._manager = SimpleNamespace(_cli_ref=CLI())
    plugin.command("on renderer")
    path = path_for(plugin)
    args = start_write(plugin, path)
    snapshot = display.capture_local_edit_snapshot("write_file", args)
    finish_write(plugin, args, text)
    rows = []
    display.render_edit_diff_with_delta("write_file", json.dumps({"success": True}),
        function_args=args, snapshot=snapshot, print_fn=rows.append)
    rows = [re.sub(r"\x1b\[[0-9;]*m", "", row) for row in rows]
    body = [row[1:] for row in rows if row.startswith("+") and not row.startswith("+++ ")]
    complete = body == text.splitlines() and not any("omitted" in row for row in rows)
    expected = len(text.splitlines()) <= 78 and "++ hidden" not in text
    assert complete is expected
    assert plugin._shown_in_full(state(plugin), str(path), _digest(path)) is expected


def test_submitted_digest_must_match_the_receipt(wo):
    path = write(wo)
    assert wo._shown_in_full(state(wo), str(path), _digest(path)) is True
    assert wo._shown_in_full(state(wo), str(path), "0" * 64) is False


def test_tracking_error_never_blocks_the_plan_write(wo, monkeypatch):
    def boom(*args):
        raise OSError("lexists failed")

    monkeypatch.setattr(pm.os.path, "lexists", boom)
    path = path_for(wo)
    assert wo.pre_tool_call("write_file", {"path": str(path), "content": "# Plan\n"}, **{**IDS, "tool_call_id": "w"}) is None
    finish_write(wo, {"path": str(path), "content": "# Plan\n"}, "# Plan\n", call="w")
    assert str(path) not in state(wo).get("fresh_writes", {})


def test_note_anchors_survive_step_numbers_in_the_plans_path():
    plans = "/work/2. drafts/3. plans/.hermes/plans"
    note = render.planning_note(plans, datetime(2026, 10, 14, 12, 0), levers={"write_once"}, classic_cli=True)
    first, step2, step3 = note.split("\n")[0], note.split("\n2. ")[1], note.split("\n3. ")[1]
    assert plans in first and plans in step2
    assert step2.startswith("Use write_file to write the plan to a NEW file under ")
    assert step3.startswith("Do not restate the plan in your reply")
    assert note.count("\n3. ") == 1 and "\n4. " in note

