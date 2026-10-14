"""0.6.2: complete display binds approval to one captured plan revision."""
import builtins
import copy
import hashlib
import json
from pathlib import Path

import pytest

from plan_mode import plugin as pm, render
from test_approval_card import KEEP, answer_card, ask_args, card, state, submit
from test_levers import IDS, make_plugin, write_plan
from test_plan_mode import session_env


CHANGED = "The plan file changed after rev 1 was submitted (writer unknown). Read the current file, keep its changes, apply the feedback, and submit a complete new revision."
LINK = "Include a markdown link to the plan file in your plan reply."


def prepared(make_plugin, session_env, levers="approval_card"):
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    plugin = make_plugin(levers)
    path = write_plan(plugin)
    if levers:
        submit(plugin, path)
    else:
        plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
        plugin._ledger.take(state(plugin)["submission"]["rule_key"])
        json.loads(plugin.tool({"action": "submit"}))
    return plugin, path


@pytest.mark.parametrize("levers", ["", "approval_card"])
@pytest.mark.parametrize("size", [3501, 65536, 65537])
def test_show_complete_single_read(make_plugin, session_env, monkeypatch, levers, size):
    plugin, path = prepared(make_plugin, session_env, levers)
    text = "é" * (size // 2) + ("x" if size % 2 else "")
    path.write_text(text)
    real_open, reads = builtins.open, []
    def counted(target, *args, **kwargs):
        if str(target) == str(path):
            reads.append(target)
        return real_open(target, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", counted)
    shown = plugin.command("show")
    assert len(reads) == 1
    if size > 65536:
        assert "truncated: showing 65536" in shown and "review" not in state(plugin)
    else:
        assert shown.endswith(text) and "truncated" not in shown
        assert state(plugin)["review"] == dict(activation_id=state(plugin)["activation_id"],
            path=str(path), revision=1, digest=hashlib.sha256(text.encode()).hexdigest(), text=text)


@pytest.mark.parametrize("platform", ["telegram", "slack", "discord", "whatsapp", "signal", "mattermost", "google_chat", "matrix", "feishu", "teams", "future_chat"])
def test_chat_show_does_not_arm(make_plugin, session_env, platform):
    plugin, path = prepared(make_plugin, session_env)
    session_env["HERMES_SESSION_PLATFORM"] = platform
    shown = plugin.command("show")
    assert path.read_text() in shown and "not armed" in shown
    assert "review" not in state(plugin)


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("levers", ["", "approval_card"])
def test_edited_approval_captures_full_snapshot(make_plugin, session_env, monkeypatch, explicit, levers):
    plugin, path = prepared(make_plugin, session_env, levers)
    text = "# Revised\n" + "x" * 12000 + "\nKeep the decisive change.\n"
    path.write_text(text)
    assert plugin.command("show").endswith(text)
    digest = state(plugin)["review"]["digest"]
    real_open, reads = builtins.open, []
    def racing(target, *args, **kwargs):
        if str(target) == str(path):
            reads.append(target)
        return real_open(target, *args, **kwargs)
    monkeypatch.setattr(builtins, "open", racing)
    assert "Plan approved" in plugin.command("approve" + (f" {path.name}" if explicit else ""))
    current = state(plugin)
    assert len(reads) == 1
    assert current["revision"] == current["approved_revision"] == 2
    assert current["approved_path"] == str(path)
    assert current["submission"]["status"] == "approved"
    assert current["submission"]["digest"] == digest
    assert current["submission"]["path"] == str(path)
    assert "review" not in current
    snapshot = path.parent / "approved" / "parser-rev2.md"
    assert snapshot.read_text() == text
    note = current["pending_note"]
    assert note.startswith(f"The user approved revised plan rev 2 of {path} (it replaces rev 1). Implement this approved text; preserve its changes; if something conflicts, ask rather than restoring the old plan.")
    assert text in note and f"If this note was shortened, read {snapshot} in full before implementing." in note
    path.write_text("Later unapproved change")
    assert text in plugin._approved_excerpt(current["submission"])
    assert "Later unapproved" not in plugin._approved_excerpt(current["submission"])
    plugin.command("approve")
    assert state(plugin)["revision"] == state(plugin)["approved_revision"] == 2
    assert not (snapshot.parent / "parser-rev3.md").exists()


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("show", [False, True])
def test_changed_plan_requires_fresh_review(make_plugin, session_env, explicit, show):
    plugin, path = prepared(make_plugin, session_env)
    path.write_text("# Changed")
    if show:
        plugin.command("show")
        path.write_text("# Changed again")
    before = copy.deepcopy(state(plugin))
    reply = plugin.command("approve" + (f" {path.name}" if explicit else ""))
    assert "/planmode show" in reply and "Plan approved" not in reply
    assert state(plugin) == before


def track(plugin, path, text):
    assert plugin.pre_tool_call("write_file", {"path": str(path)}) is None
    path.write_text(text)


def test_review_is_bound_to_target(make_plugin, session_env):
    plugin, a = prepared(make_plugin, session_env)
    a.write_text("Same changed content")
    b = a.with_name("b.md")
    track(plugin, b, a.read_text())
    plugin.command(f"show {a}")
    assert "never submitted" in plugin.command("approve")
    assert "Plan approved" not in plugin.command(f"approve {b}")
    assert "Plan approved" in plugin.command(f"approve {a}")
    assert state(plugin)["approved_path"] == str(a)


def test_never_submitted_explicit_requires_own_review(make_plugin, session_env):
    plugin, a = prepared(make_plugin, session_env)
    b = a.with_name("b.md")
    track(plugin, b, "# New candidate")
    assert "/planmode show" in plugin.command(f"approve {b}")
    plugin.command(f"show {b}")
    assert "Plan approved" in plugin.command(f"approve {b}")
    assert state(plugin)["approved_path"] == str(b) and state(plugin)["approved_revision"] == 2


def test_never_submitted_without_any_submission(make_plugin, session_env):
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    plugin = make_plugin()
    path = write_plan(plugin)
    assert "/planmode show" in plugin.command(f"approve {path}")
    plugin.command("show")
    assert "Plan approved" in plugin.command(f"approve {path}")
    assert state(plugin)["submission"]["status"] == "approved"
    assert state(plugin)["approved_revision"] == 1


@pytest.mark.parametrize("invalid", ["activation", "path"])
def test_review_metadata_must_match(make_plugin, session_env, invalid):
    plugin, path = prepared(make_plugin, session_env)
    path.write_text("# Changed")
    plugin.command("show")
    current = state(plugin)
    current["review"]["activation_id" if invalid == "activation" else "path"] = "other"
    plugin._save_state("sk:unit-session", current)
    assert "Plan approved" not in plugin.command(f"approve {path}")
    assert state(plugin)["active"]


def test_show_an_identical_unsubmitted_file_still_pins_path(make_plugin, session_env):
    plugin, a = prepared(make_plugin, session_env)
    b = a.with_name("b.md")
    track(plugin, b, a.read_text())
    assert "/planmode show" in plugin.command(f"approve {b}")
    plugin.command(f"show {b}")
    assert "Plan approved" in plugin.command(f"approve {b}")
    assert state(plugin)["approved_path"] == str(b) and state(plugin)["approved_revision"] == 2


def test_snapshot_save_failure_keeps_approval_pending(make_plugin, session_env, monkeypatch):
    plugin, path = prepared(make_plugin, session_env)
    path.write_text("# Changed")
    plugin.command("show")
    before = copy.deepcopy(state(plugin))
    real_open = Path.open
    def fail_snapshot(target, *args, **kwargs):
        if target.parent.name == "approved":
            raise OSError("snapshot unavailable")
        return real_open(target, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail_snapshot)
    assert "snapshot could not be saved" in plugin.command("approve")
    assert state(plugin) == before


@pytest.mark.parametrize("levers", ["", "approval_card"])
@pytest.mark.parametrize("explicit", [False, True])
def test_changed_approval_is_idle_only(make_plugin, session_env, levers, explicit):
    plugin, path = prepared(make_plugin, session_env, levers)
    path.write_text("# Changed")
    plugin.command("show")
    if levers:
        current = state(plugin)
        ask = {"clarify": current["submission"]["clarify"]}
        assert plugin.pre_tool_call("clarify", ask_args(ask), tool_call_id="open") is None
    else:
        plugin._ledger.mark_inflight(state(plugin)["submission"]["rule_key"], "submit")
    before = copy.deepcopy(state(plugin))
    reply = plugin.command("approve" + (f" {path}" if explicit else ""))
    assert "finish or dismiss the open approval first" in reply.lower()
    assert state(plugin) == before


@pytest.mark.parametrize("action", ["reject feedback", "off", "done", "on", "submit", "show other.md", "show missing.md", "oversize", "approve"])
def test_review_invalidation(make_plugin, session_env, action):
    plugin, path = prepared(make_plugin, session_env)
    other = path.with_name("other.md")
    track(plugin, other, "# Other")
    plugin.command(f"show {path}")
    assert "review" in state(plugin)
    if action == "submit":
        submit(plugin, path)
    elif action == "oversize":
        path.write_text("x" * 65537)
        plugin.command(f"show {path}")
    elif action == "approve":
        assert "Plan approved" in plugin.command(f"approve {path}")
    else:
        plugin.command(action)
    if action == "show other.md":
        assert state(plugin)["review"]["path"] == str(other)
        assert state(plugin)["review"]["text"] == "# Other"
    else:
        assert "review" not in state(plugin)


@pytest.mark.parametrize("answer", [KEEP, "Keep the change and add tests"])
def test_keep_planning_preserves_disk_changes(card, answer):
    plugin, path, result = card
    path.write_text("# Changed")
    plugin.command("show")
    same_turn = answer_card(plugin, result, answer)
    assert CHANGED in same_turn
    assert CHANGED in state(plugin)["pending_note"]
    assert "review" not in state(plugin)
    assert CHANGED in plugin.pre_llm_call()["context"]


def test_next_turn_detects_change_without_card(make_plugin, session_env):
    plugin, path = prepared(make_plugin, session_env, "")
    path.write_text("# Changed")
    assert CHANGED in plugin.pre_llm_call()["context"]


def test_edit_instructions_and_ui_note(make_plugin, session_env, monkeypatch):
    plugin, path = prepared(make_plugin, session_env)
    assert plugin.command("edit") == (f"{path}\nEdit and save it in your editor (GUI editors: wait for the save). "
                                      f"Then /planmode show {path}, then /planmode approve {path}.")
    assert plugin.command(f"edit {path.name}").startswith(str(path))
    other = path.with_name("other.md")
    assert plugin.pre_tool_call("write_file", {"path": str(other)}) is None
    other.write_text("# Other\n")
    assert plugin.command("edit other.md").endswith(f"Then /planmode show {other}, then /planmode approve {other}.")
    nested = path.parent / "nested" / path.name  # same name as the submitted plan, in a subdirectory
    nested.parent.mkdir()
    assert plugin.pre_tool_call("write_file", {"path": str(nested)}) is None
    nested.write_text("# Nested\n")
    reply = plugin.command(f"edit {nested}")
    assert reply.endswith(f"Then /planmode show {nested}, then /planmode approve {nested}.")
    assert plugin.command(f"show {nested}").endswith("# Nested\n")
    assert state(plugin)["review"]["path"] == str(nested)
    assert "not written by this session" in plugin.command("edit missing.md")
    assert LINK not in plugin.pre_llm_call()["context"]
    current = state(plugin)
    plugin._save_state("ui:editing-tab", current)
    session_env["HERMES_UI_SESSION_ID"] = "editing-tab"
    monkeypatch.setattr(pm, "_tui_ui_session_id", lambda: "editing-tab")
    note = plugin.pre_llm_call()["context"]
    assert LINK in note


def test_ui_session_arms_even_with_desktop_surface(make_plugin, session_env, monkeypatch):
    plugin, path = prepared(make_plugin, session_env)
    current = state(plugin)
    plugin._save_state("ui:editing-tab", current)
    session_env.update(HERMES_UI_SESSION_ID="editing-tab", HERMES_SESSION_PLATFORM="desktop")
    monkeypatch.setattr(pm, "_session_context_is_engaged", lambda: True)
    monkeypatch.setattr(pm, "_tui_ui_session_id", lambda: "editing-tab")
    assert plugin.command("show").endswith(path.read_text())
    assert plugin._load_state("ui:editing-tab")["review"]["path"] == str(path)


def test_ui_planning_note_golden():
    from datetime import datetime
    note = render.planning_note("/plans", datetime(2026, 10, 14, 12, 0), ui_session=True)
    expected = (Path(__file__).parent / "fixtures/planning_ui_062.txt").read_text()
    assert note == expected.rstrip("\n")


@pytest.mark.parametrize("error", [OSError, ValueError])
def test_unreadable_or_oversized_submitted_plan_still_gets_the_changed_note(make_plugin, session_env, monkeypatch, error):
    plugin, path = prepared(make_plugin, session_env)
    def fail(_path):
        raise error("too large or gone")
    monkeypatch.setattr(pm, "plan_digest", fail)
    assert "The plan file changed after rev 1 was submitted" in plugin._changed_plan_note(state(plugin))


def test_failed_snapshot_write_leaves_no_partial_file(make_plugin, session_env):
    plugin, path = prepared(make_plugin, session_env)
    text = "# Revised\n" + "y" * 5000 + "\n"
    path.write_text(text)
    plugin.command("show")
    current = state(plugin)
    good = current["review"]["text"]
    current["review"]["text"] = good + "\ud800"  # cannot be encoded: the write fails after the exclusive open
    plugin._save_state("sk:unit-session", current)
    assert "could not be saved" in plugin.command("approve")
    approved = path.parent / "approved"
    assert not any(approved.glob("*")) if approved.exists() else True
    current = state(plugin)
    current["review"]["text"] = good
    plugin._save_state("sk:unit-session", current)
    assert "Plan approved" in plugin.command("approve")
    assert (approved / f"{path.stem}-rev2.md").read_bytes() == text.encode()
