"""#67: an unbound gateway reset must also find finished, idle plans."""
import copy
import json

import pytest

from plan_mode import plugin as pm
from test_approval_card import answer_card
from test_levers import make_plugin
from test_plan_mode import session_env


@pytest.fixture
def idle_chat(make_plugin, session_env, tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "_edit_review_available", lambda: False)
    plugin = make_plugin("approval_card,reentry,todo_batch", plan_profile="lean")
    session_env["HERMES_SESSION_PLATFORM"] = "telegram"

    def finish(chat="chat-1", session_id="s1", record_at="executing_llm"):
        session_env["HERMES_SESSION_KEY"] = chat
        assert "Plan mode is on" in plugin.command("on reset regression")
        state = plugin._load_state("sk:" + chat)
        path = tmp_path / ".hermes" / "plans" / (chat + ".md")
        assert str(path.parent) == state["plans_dir"]
        if record_at == "planning_llm":
            plugin.pre_llm_call(session_id=session_id, platform="telegram")
        args = {"path": str(path), "content": "# Reset plan\n\n1. Implement\n2. Verify\n"}
        ids = {"session_id": session_id} if record_at == "planning_tool" else {}
        assert plugin.pre_tool_call("write_file", args, tool_call_id="write", **ids) is None
        path.write_text(args["content"], encoding="utf-8")
        plugin.post_tool_call("write_file", args, tool_call_id="write", status="ok", **ids)
        submit_args = {"action": "submit", "path": str(path)}
        assert plugin.pre_tool_call("plan_mode", submit_args, tool_call_id="submit") is None
        card = json.loads(plugin.tool(submit_args))
        answer_card(plugin, card)
        state = plugin._load_state("sk:" + chat)
        assert not state["active"] and state["phase"] == "executing"
        if record_at == "executing_llm":
            plugin.pre_llm_call(session_id=session_id, platform="telegram")
        plugin.post_llm_call(platform="telegram")
        state = plugin._load_state("sk:" + chat)
        assert not state["active"] and state.get("phase") != "executing"
        assert state["revision"] == 1 and state["submission"]["path"] == str(path)
        assert state["plan_files"] and state["session_id_hash"] == plugin._session_id_hash(session_id)
        storage = pm._state_storage_key("sk:" + chat)
        assert storage not in plugin._active_storage_keys() + plugin._executing_storage_keys()
        return copy.deepcopy(state)

    return plugin, finish


def unbound_reset(plugin, session_env, old_session_id):
    session_env["HERMES_SESSION_KEY"] = ""
    plugin.on_session_reset(platform="telegram", old_session_id=old_session_id, new_session_id="new-session")


@pytest.mark.parametrize("record_at", ["planning_llm", "planning_tool", "executing_llm"])
def test_067_idle_reset_starts_a_fresh_plan(idle_chat, session_env, record_at):
    plugin, finish = idle_chat
    finish(record_at=record_at)

    unbound_reset(plugin, session_env, "s1")
    session_env["HERMES_SESSION_KEY"] = "chat-1"
    assert "Plan mode is on" in plugin.command("on next task")

    state = plugin._load_state("sk:chat-1")
    assert state["revision"] == 0
    assert not state.get("previous_plan")
    assert state["plan_files"] == []
    assert not state.get("approved_path")
    assert pm._state_storage_key("sk:chat-1") not in plugin.ctx.state.get("session-index", [])


def test_067_unmatched_reset_preserves_idle_state(idle_chat, session_env):
    plugin, finish = idle_chat
    before = finish()

    unbound_reset(plugin, session_env, "unknown-session")

    assert plugin._load_state("sk:chat-1") == before


def test_067_idle_reset_isolates_chats(idle_chat, session_env):
    plugin, finish = idle_chat
    finish("chat-1", "s1", "planning_tool")
    second = finish("chat-2", "s2", "executing_llm")

    unbound_reset(plugin, session_env, "s1")

    assert plugin._load_state("sk:chat-1") == {}
    assert plugin._load_state("sk:chat-2") == second
    assert plugin.ctx.state.get("session-index", []) == [pm._state_storage_key("sk:chat-2")]


def test_067_session_index_is_bounded_and_refreshes_recency(idle_chat):
    plugin, _ = idle_chat
    keys = ["sk:recent-" + str(index) for index in range(258)]
    for key in keys:
        plugin._remember_session_id(key, {}, key)
        assert len(plugin.ctx.state.get("session-index", [])) <= 256
    expected = [pm._state_storage_key(key) for key in keys[-256:]]
    assert plugin.ctx.state.get("session-index", []) == expected

    # Re-observing the same id must refresh recency even when its hash is unchanged.
    key = keys[2]
    plugin._remember_session_id(key, plugin._load_state(key), key)
    expected.append(expected.pop(0))
    assert plugin.ctx.state.get("session-index", []) == expected
    plugin._remember_session_id("sk:newest", {}, "newest")
    expected = expected[1:] + [pm._state_storage_key("sk:newest")]
    assert plugin.ctx.state.get("session-index", []) == expected
    assert all(isinstance(key, str) and key.startswith("session:") for key in expected)

    plugin._clear_storage_key(expected[-1])
    assert plugin.ctx.state.get("session-index", []) == expected[:-1]


def test_067_same_session_reentry_keeps_revision_and_previous_plan(idle_chat):
    plugin, finish = idle_chat
    before = finish()

    assert "Plan mode is on" in plugin.command("on continue this chat")

    state = plugin._load_state("sk:chat-1")
    assert state["revision"] == before["revision"] == 1
    assert state["previous_plan"] == before["submission"]["path"]
    assert state["plan_files"] == before["plan_files"]


def test_067_session_index_skips_redundant_writes(idle_chat, monkeypatch):
    plugin, _ = idle_chat
    plugin._remember_session_id("sk:a", {}, "a")
    plugin._remember_session_id("sk:b", {}, "b")
    writes = []
    real_set = plugin.ctx.state.set
    monkeypatch.setattr(plugin.ctx.state, "set",
                        lambda key, value: (writes.append(key), real_set(key, value))[1])
    plugin._mark_session(pm._state_storage_key("sk:b"), True)   # already most recent
    plugin._mark_session(pm._state_storage_key("sk:zz"), False)  # never indexed
    assert "session-index" not in writes
    plugin._mark_session(pm._state_storage_key("sk:a"), True)   # moves to the end
    assert writes.count("session-index") == 1
    assert plugin.ctx.state.get("session-index", [])[-1] == pm._state_storage_key("sk:a")


def as_pre_index_store(plugin, tmp_path):
    """Make the in-memory store look like a 0.6.7 state.json: no session index, no backfill marker."""
    plugin.ctx.state.values.pop("session-index", None)
    plugin.ctx.state.values.pop("session-index-backfill", None)
    path = tmp_path / "state.json"
    path.write_text(json.dumps(plugin.ctx.state.values), encoding="utf-8")
    plugin.ctx.state.path = path
    return path


def test_067_upgrade_backfills_idle_states_written_before_the_index(idle_chat, session_env, tmp_path):
    plugin, finish = idle_chat
    finish("chat-1", "s1", "executing_llm")
    second = finish("chat-2", "s2", "planning_tool")
    as_pre_index_store(plugin, tmp_path)
    assert plugin.ctx.state.get("session-index") is None

    unbound_reset(plugin, session_env, "s1")  # the first action after the upgrade is /new

    assert plugin._load_state("sk:chat-1") == {}
    assert plugin._load_state("sk:chat-2") == second
    assert plugin.ctx.state.get("session-index") == [pm._state_storage_key("sk:chat-2")]
    assert plugin.ctx.state.get("session-index-backfill") is True


def test_067_backfill_runs_once_and_keeps_recent_entries_last(idle_chat, session_env, tmp_path):
    plugin, finish = idle_chat
    finish("chat-1", "s1")
    path = as_pre_index_store(plugin, tmp_path)
    plugin._remember_session_id("sk:after-upgrade", {}, "s9")  # indexed by 0.6.8 before the first reset

    unbound_reset(plugin, session_env, "unknown-session")

    recent = pm._state_storage_key("sk:after-upgrade")
    assert plugin.ctx.state.get("session-index") == [pm._state_storage_key("sk:chat-1"), recent]
    path.write_text("not json", encoding="utf-8")  # a second scan would fail loudly here
    unbound_reset(plugin, session_env, "unknown-session")
    assert plugin.ctx.state.get("session-index")[-1] == recent


def test_067_backfill_tolerates_a_store_without_a_readable_file(idle_chat, session_env, tmp_path):
    plugin, finish = idle_chat
    before = finish("chat-1", "s1")
    path = as_pre_index_store(plugin, tmp_path)
    path.write_text("not json", encoding="utf-8")

    unbound_reset(plugin, session_env, "s1")  # unreadable: no crash, no marker, retried next time

    assert plugin._load_state("sk:chat-1") == before
    assert plugin.ctx.state.get("session-index-backfill") is None
    path.write_text(json.dumps(plugin.ctx.state.values), encoding="utf-8")
    unbound_reset(plugin, session_env, "s1")
    assert plugin._load_state("sk:chat-1") == {}
