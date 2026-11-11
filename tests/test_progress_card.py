"""Display-only live progress, using disposable approved plans and fake host messages."""
import copy
import json
import threading
import time

import pytest

from plan_mode import render
from plan_mode import plugin as pm
from plan_mode.progress_card import ProgressCards
from test_approval_card import APPROVE, answer_card, card, state  # noqa: F401 (fixtures)
from test_finish_receipt import building  # noqa: F401 (fixture)
from test_levers import make_plugin, write_plan  # noqa: F401 (fixtures)
from test_plan_mode import session_env  # noqa: F401 (fixture)


class Handle:
    ok = True

    def __init__(self):
        self.edits = []

    def edit(self, text):
        self.edits.append(text)
        return {"ok": True}


class Messages:
    def __init__(self, plugin):
        self.plugin = plugin
        self.posts = []
        self.handles = []
        self.threads = []

    def post(self, text):
        assert not self.plugin._lock._is_owned()
        self.posts.append(text)
        self.threads.append(threading.current_thread())
        handle = Handle()
        self.handles.append(handle)
        return handle


@pytest.fixture
def chat(building, session_env):
    plugin, path = building
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"
    messages = Messages(plugin)
    plugin.ctx.post_message = messages.post
    yield plugin, path, messages
    assert plugin._progress_cards.drain(3)


def todos(*statuses):
    return [{"id": str(i), "content": f"Step {i}", "status": status} for i, status in enumerate(statuses)]


def write_todos(plugin, items, tool="todo_list", **kwargs):
    return plugin.post_tool_call(tool, {"todos": items}, turn_id="t1", status="ok",
                                 result=json.dumps({"todos": items}), **kwargs)


def test_one_card_tracks_progress_and_finishes(chat):
    plugin, _, messages = chat
    assert write_todos(plugin, todos("in_progress", "pending", "pending")) is None
    assert messages.posts == ["Plan rev 1: 0 of 3 done\n▶ Step 0\n○ Step 1\n○ Step 2"]
    assert messages.threads == [threading.current_thread()]
    items = todos("completed", "in_progress", "pending")
    write_todos(plugin, items)
    assert plugin._progress_cards.drain(2)
    handle = messages.handles[0]
    assert len(handle.edits) == 1 and handle.edits[0].startswith("Plan rev 1: 1 of 3 done")
    write_todos(plugin, items)
    assert plugin._progress_cards.drain(2)
    assert len(handle.edits) == 1
    write_todos(plugin, todos("completed", "completed", "completed"))
    assert plugin._progress_cards.drain(2)
    assert len(messages.posts) == 1 and len(handle.edits) == 2
    assert handle.edits[-1].startswith("✅ Plan rev 1 done: 3 of 3 steps")
    assert state(plugin)["finish_pending"] is True


@pytest.mark.parametrize("surface,eligible", [
    ("telegram", True), ("discord", True), ("slack", True), ("desktop", True), ("tui", True),
    ("DeSkToP", True), ("cli", False), ("", False), ("local", False), ("terminal", False),
    ("api_server", False), ("webhook", False), ("cron", False), ("batch", False),
    ("subagent", False), ("curator", False), ("acp", False), ("dashboard", False),
    # Platforms without in-place edits would get a new message per update.
    ("signal", False), ("sms", False), ("email", False), ("matrix", True), ("mattermost", True),
])
def test_surfaces(chat, session_env, surface, eligible):
    plugin, _, messages = chat
    session_env["HERMES_SESSION_PLATFORM"] = surface
    write_todos(plugin, todos("pending"))
    assert len(messages.posts) == int(eligible)


@pytest.mark.parametrize("surface,environment,eligible", [("desktop", "local", True), ("cli", "telegram", False)])
def test_host_surface_wins(chat, session_env, surface, environment, eligible):
    plugin, _, messages = chat
    session_env["HERMES_SESSION_PLATFORM"] = environment
    plugin.ctx.session_surface = lambda: surface
    write_todos(plugin, todos("pending"))
    assert len(messages.posts) == int(eligible)


@pytest.mark.parametrize("surface", [None, "", 42])
def test_invalid_host_surface_falls_back(chat, surface):
    plugin, _, messages = chat
    plugin.ctx.session_surface = lambda: surface
    write_todos(plugin, todos("pending"))
    assert len(messages.posts) == 1


@pytest.mark.parametrize("setting,eligible", [(False, False), (True, True), (0, True), (None, True)])
def test_setting_only_false_disables(chat, setting, eligible):
    plugin, _, messages = chat
    plugin.ctx.settings["progress_card"] = setting
    write_todos(plugin, todos("pending"))
    assert len(messages.posts) == int(eligible)


def test_missing_post_preserves_state(chat):
    plugin, _, messages = chat
    before = copy.deepcopy(state(plugin))
    items = todos("completed", "pending")
    write_todos(plugin, items)
    expected = copy.deepcopy(state(plugin))
    plugin._save_state("sk:unit-session", before)
    del plugin.ctx.post_message
    assert write_todos(plugin, items) is None
    assert state(plugin) == expected
    assert expected["progress"] == {"total": 2, "completed": 1, "cancelled": 0, "current": ""}
    assert len(messages.posts) == 1


@pytest.mark.parametrize("raises", [False, True])
def test_failed_post_is_not_retried_and_preserves_state(chat, raises):
    plugin, _, messages = chat
    calls = []

    def post(text):
        calls.append(text)
        if raises:
            raise RuntimeError("unavailable")
        handle = Handle()
        handle.ok = False
        return handle

    plugin.ctx.settings["progress_card"] = False
    before = copy.deepcopy(state(plugin))
    write_todos(plugin, todos("pending", "pending"))
    expected = copy.deepcopy(state(plugin))
    plugin._save_state("sk:unit-session", before)
    plugin.ctx.settings["progress_card"] = True
    plugin.ctx.post_message = post
    assert write_todos(plugin, todos("pending", "pending")) is None
    assert state(plugin) == expected
    assert write_todos(plugin, todos("completed", "pending")) is None
    assert state(plugin)["progress"]["completed"] == 1
    assert len(calls) == 1 and not messages.posts


def test_slow_edit_does_not_block_hook(chat):
    plugin, _, messages = chat
    write_todos(plugin, todos("pending", "pending"))
    entered = threading.Event()

    def slow_edit(text):
        entered.set()
        time.sleep(1)
        messages.handles[0].edits.append(text)
        return {"ok": True}

    messages.handles[0].edit = slow_edit
    start = time.monotonic()
    write_todos(plugin, todos("completed", "pending"))
    elapsed = time.monotonic() - start
    assert entered.wait(1)
    assert elapsed < 0.5
    assert not plugin._progress_cards.drain(0.01)
    assert plugin._progress_cards.drain(2)


def test_planning_todos_never_post(card, session_env):
    plugin, _, _ = card
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"
    messages = Messages(plugin)
    plugin.ctx.post_message = messages.post
    write_todos(plugin, todos("pending"))
    assert not messages.posts and "progress" not in state(plugin)


def test_only_successful_todo_writes_post(chat):
    plugin, _, messages = chat
    result = json.dumps({"todos": todos("pending")})
    plugin.post_tool_call("todo_list", {}, status="ok", result=result)
    plugin.post_tool_call("todo_list", {"todos": todos("pending")}, status="error", result=result)
    plugin.post_tool_call("other", {"todos": todos("pending")}, status="ok", result=result)
    assert not messages.posts and "progress" not in state(plugin)
    write_todos(plugin, todos("pending"), tool="todo")
    assert len(messages.posts) == 1


def test_new_revision_and_path_get_new_cards(chat):
    plugin, path, messages = chat
    write_todos(plugin, todos("pending", "pending"))
    current = state(plugin)
    current["approved_revision"] = 2
    plugin._save_state("sk:unit-session", current)
    write_todos(plugin, todos("completed", "pending"))
    current = state(plugin)
    current["approved_path"] = str(path.with_name("another.md"))
    plugin._save_state("sk:unit-session", current)
    write_todos(plugin, todos("completed", "pending"))
    assert len(messages.posts) == 3
    assert messages.posts[1].startswith("Plan rev 2: 1 of 2 done")
    assert not any(handle.edits for handle in messages.handles)


def test_classic_completion_keeps_final_card(chat):
    plugin, _, messages = chat
    plugin.ctx.settings["levers"] = "approval_card"
    write_todos(plugin, todos("completed"))
    assert messages.posts == ["✅ Plan rev 1 done: 1 of 1 steps\n✓ Step 0"]
    assert "phase" not in state(plugin)


def test_classic_reapproval_posts_separate_cards(chat):
    plugin, path, messages = chat
    plugin.ctx.settings["levers"] = "approval_card"
    executing = state(plugin)
    executing["approved_revision"] = None
    executing["approved_at"] = "2026-11-11T00:00:00Z"
    plugin._save_state("sk:unit-session", executing)
    write_todos(plugin, todos("in_progress", "pending"))
    write_todos(plugin, todos("completed", "completed"))
    assert plugin._progress_cards.drain(2)
    assert "phase" not in state(plugin)
    first_edits = list(messages.handles[0].edits)
    assert first_edits[-1] == "✅ Plan done: 2 of 2 steps\n✓ Step 0\n✓ Step 1"

    executing["approved_at"] = "2026-11-11T00:00:01Z"
    plugin._save_state("sk:unit-session", executing)
    assert state(plugin)["approved_path"] == str(path)
    assert state(plugin)["approved_revision"] is None
    write_todos(plugin, todos("in_progress", "pending"))
    write_todos(plugin, todos("completed", "in_progress"))
    assert plugin._progress_cards.drain(2)
    write_todos(plugin, todos("completed", "completed"))
    assert plugin._progress_cards.drain(2)
    assert "phase" not in state(plugin)
    assert messages.posts == ["Plan: 0 of 2 done\n▶ Step 0\n○ Step 1"] * 2
    assert messages.handles[0].edits == first_edits
    assert messages.handles[1].edits[-1] == "✅ Plan done: 2 of 2 steps\n✓ Step 0\n✓ Step 1"


@pytest.mark.parametrize("child,platform", [(True, "telegram"), (False, "SuBaGeNt"), (False, "CuRaToR")])
def test_delegated_todos_preserve_state_without_card_delivery(chat, monkeypatch, child, platform):
    plugin, _, messages = chat
    plugin.ctx.session_surface = lambda: "telegram"  # Child inherits the parent's host binding.
    write_todos(plugin, todos("pending"))
    before = copy.deepcopy(state(plugin))
    items = todos("completed")
    plugin.ctx.settings["progress_card"] = False
    assert write_todos(plugin, items, platform=platform) is None
    expected = copy.deepcopy(state(plugin))
    plugin._save_state("sk:unit-session", before)
    plugin.ctx.settings["progress_card"] = True
    monkeypatch.setattr(pm, "_delegated_child", lambda: child)
    assert write_todos(plugin, items, platform=platform) is None
    assert state(plugin) == expected
    assert expected["progress"]["completed"] == 1
    assert plugin._progress_cards.drain(2)
    assert len(messages.posts) == 1 and not messages.handles[0].edits


def test_out_of_order_hook_delivery_keeps_completion(chat, monkeypatch):
    plugin, _, messages = chat
    captured = []
    deliver = plugin._update_progress_card
    monkeypatch.setattr(plugin, "_update_progress_card", lambda *update: captured.append(update))
    write_todos(plugin, todos("pending"))
    write_todos(plugin, todos("completed"))
    assert len(captured) == 2 and captured[0][-1] < captured[1][-1]
    deliver(*captured[1])
    deliver(*captured[0])
    assert plugin._progress_cards.drain(2)
    assert messages.posts == ["✅ Plan rev 1 done: 1 of 1 steps\n✓ Step 0"]
    assert not messages.handles[0].edits


def test_reentrant_hook_preserves_state_without_posting(chat):
    plugin, _, messages = chat
    before = copy.deepcopy(state(plugin))
    plugin.ctx.settings["progress_card"] = False
    write_todos(plugin, todos("completed"))
    expected = copy.deepcopy(state(plugin))
    plugin._save_state("sk:unit-session", before)
    plugin.ctx.settings["progress_card"] = True
    attempted = []
    plugin.ctx.post_message = lambda text: attempted.append(text)
    with plugin._lock:
        assert write_todos(plugin, todos("completed")) is None
    assert state(plugin) == expected
    assert not attempted and not messages.posts


def test_edit_review_without_approved_plan_preserves_state_without_posting(chat):
    plugin, _, messages = chat
    current = state(plugin)
    current.pop("phase", None)
    current.pop("approved_path", None)
    current["edit_review"] = {"key": "plan-mode-edit:test"}
    plugin._save_state("sk:unit-session", current)
    assert write_todos(plugin, todos("pending")) is None
    after = state(plugin)
    assert after["progress"]["total"] == 1
    assert after["edit_review"] == current["edit_review"]
    assert not messages.posts


def test_sessions_sharing_state_get_separate_cards(chat, monkeypatch):
    plugin, _, messages = chat
    shared = state(plugin)
    monkeypatch.setattr(plugin, "_state_for_hook", lambda identity: ("sk:unit-session", shared))
    monkeypatch.setattr(pm, "derive_session_identity", lambda platform: pm.SessionIdentity("sk:first", surface="telegram"))
    write_todos(plugin, todos("pending", "pending"))
    monkeypatch.setattr(pm, "derive_session_identity", lambda platform: pm.SessionIdentity("sk:second", surface="telegram"))
    write_todos(plugin, todos("completed", "pending"))
    assert plugin._progress_cards.drain(2)
    assert len(messages.posts) == 2
    assert messages.posts[0].startswith("Plan rev 1: 0 of 2 done")
    assert messages.posts[1].startswith("Plan rev 1: 1 of 2 done")
    assert not any(handle.edits for handle in messages.handles)


def test_render_limit_and_content():
    items = todos(*(["pending"] * 10))
    items[0]["content"] = "  multi\nline\t content  "
    items[1]["content"] = "x" * 61
    lines = render.progress_card(1, json.dumps({"todos": items})).splitlines()
    assert len(lines) == 10
    assert lines[1] == "○ multi line content"
    assert lines[2] == "○ " + "x" * 59 + "…"
    assert lines[-1] == "… and 2 more"


def test_render_cancelled_and_no_revision():
    result = json.dumps({"todos": todos("completed", "cancelled", "pending")})
    assert render.progress_card(None, result) == "Plan: 1 of 3 done, 1 cancelled\n✓ Step 0\n✗ Step 1\n○ Step 2"
    result = json.dumps({"todos": todos("completed", "cancelled")})
    assert render.progress_card(2, result) == "✅ Plan rev 2 done: 1 of 2 steps (1 cancelled)\n✓ Step 0\n✗ Step 1"


def test_render_window_contains_late_running_step():
    items = todos(*(["completed"] * 9), "in_progress", "pending", "pending")
    assert render.progress_card(1, json.dumps({"todos": items})).splitlines() == [
        "Plan rev 1: 9 of 12 done",
        "… 4 earlier",
        "✓ Step 4",
        "✓ Step 5",
        "✓ Step 6",
        "✓ Step 7",
        "✓ Step 8",
        "▶ Step 9",
        "○ Step 10",
        "○ Step 11",
    ]


def test_render_window_keeps_early_running_step():
    items = todos("completed", "in_progress", *(["pending"] * 10))
    lines = render.progress_card(1, json.dumps({"todos": items})).splitlines()
    assert lines == ["Plan rev 1: 1 of 12 done", "✓ Step 0", "▶ Step 1",
                     *[f"○ Step {i}" for i in range(2, 8)], "… and 4 more"]


def test_render_window_all_completed_starts_at_zero():
    items = todos(*(["completed"] * 12))
    lines = render.progress_card(1, json.dumps({"todos": items})).splitlines()
    assert lines == ["✅ Plan rev 1 done: 12 of 12 steps",
                     *[f"✓ Step {i}" for i in range(8)], "… and 4 more"]


@pytest.mark.parametrize("result", [None, "bad", "null", "[]", "{}", '{"todos": []}',
    '{"todos": [1]}', '{"todos": [{"content": 3, "status": "pending"}]}',
    '{"todos": [{"content": "x", "status": []}]}', '{"todos": [{"content": "x", "status": "bad"}]}'])
def test_render_malformed_is_empty(result):
    assert render.progress_card(1, result) == ""


def test_delivery_coalesces_and_uses_one_daemon():
    cards = ProgressCards()
    entered, release = threading.Event(), threading.Event()
    handle = Handle()
    threads = []

    def edit(text):
        threads.append(threading.current_thread())
        if text == "second":
            entered.set()
            assert release.wait(2)
        handle.edits.append(text)
        return {"ok": True}

    handle.edit = edit
    cards.update("key", "first", lambda text: handle, 1)
    cards.update("key", "second", None, 2)
    try:
        assert entered.wait(1)
        cards.update("key", "third", None, 3)
        cards.update("key", "latest", None, 4)
        cards.update("key", "latest", None, 5)
    finally:
        release.set()
    assert cards.drain(2)
    assert handle.edits == ["second", "latest"]
    assert len(set(threads)) == 1 and threads[0].daemon
    assert threads[0].name == "plan-mode-progress-card"


@pytest.mark.parametrize("raises", [False, True])
def test_edit_failure_keeps_handle(raises):
    cards = ProgressCards()
    handle = Handle()

    def edit(text):
        handle.edits.append(text)
        if raises:
            raise RuntimeError("unavailable")
        return {"ok": False}

    handle.edit = edit
    cards.update("key", "first", lambda text: handle, 1)
    for seq, text in enumerate(("second", "third"), 2):
        cards.update("key", text, None, seq)
        assert cards.drain(2)
    assert handle.edits == ["second", "third"]


def test_delivery_is_bounded_and_empty_updates_do_nothing():
    cards = ProgressCards()
    posts = []

    def post(text):
        posts.append(text)
        return Handle()

    cards.update("empty", "", post, 1)
    for key in range(257):
        cards.update(key, str(key), post, key + 2)
    assert len(cards._cards) == 256 and 0 not in cards._cards
    cards.update(0, "new card", post, 259)
    assert len(posts) == 258 and cards.drain(1)


def test_stale_update_during_first_post_is_ignored():
    cards = ProgressCards()
    entered, release = threading.Event(), threading.Event()
    handle = Handle()
    posts = []

    def post(text):
        posts.append(text)
        entered.set()
        assert release.wait(2)
        return handle

    thread = threading.Thread(target=cards.update, args=("key", "done", post, 2))
    thread.start()
    try:
        assert entered.wait(1)
        cards.update("key", "older", None, 1)
        cards.update("key", "same sequence", None, 2)
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive() and cards.drain(2)
    assert posts == ["done"] and not handle.edits


def test_newer_update_during_first_post_is_kept():
    cards = ProgressCards()
    handle = Handle()

    def post(text):
        cards.update("key", "done", None, 3)
        cards.update("key", "older", None, 2)
        return handle

    cards.update("key", "first", post, 1)
    assert cards.drain(2) and handle.edits == ["done"]


def test_identical_text_still_advances_sequence():
    cards = ProgressCards()
    handle = Handle()
    cards.update("key", "done", lambda text: handle, 1)
    cards.update("key", "done", None, 3)
    cards.update("key", "older", None, 2)
    cards.update("key", "same sequence", None, 3)
    assert cards.drain(2) and not handle.edits


def test_progress_card_config_schema():
    yaml = pytest.importorskip("yaml")
    from pathlib import Path
    schema = yaml.safe_load((Path(__file__).parents[1] / "plugin.yaml").read_text())["config_schema"]["progress_card"]
    assert schema["type"] == "bool" and schema["default"] is True
    assert schema["label"] == "Progress card"
