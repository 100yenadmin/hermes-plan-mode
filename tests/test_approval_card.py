"""PR-A approval decisions, using the host's real clarify result envelope."""
import copy
import json

import pytest

from plan_mode import plugin as pm
from plan_mode import render
from test_levers import IDS, make_plugin, write_plan
from test_plan_mode import session_env


APPROVE = "Approve, auto-accept edits"
KEEP = "Keep planning"
NO_CLARIFY = {"acp", "api_server", "cron", "webhook", "msgraph_webhook"}


def state(plugin):
    return plugin._load_state("sk:unit-session")


def submit(plugin, path):
    assert plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS) is None
    return json.loads(plugin.tool({"action": "submit"}))


def ask_args(card):
    return {"questions": [{**card["clarify"], "multi_select": False}]}


def host_result(card, answer=APPROVE, outcome="submitted", **response):
    return json.dumps({"responses": [{"question": card["clarify"]["question"],
                                     "choices_offered": card["clarify"]["choices"],
                                     "status": "answered", "user_response": answer, **response}],
                       "outcome": outcome})


def answer_card(plugin, card, answer=APPROVE, outcome="submitted", **response):
    args = ask_args(card)
    assert plugin.pre_tool_call("clarify", args, tool_call_id="ask") is None
    result = host_result(card, answer, outcome, **response)
    plugin.post_tool_call("clarify", args, tool_call_id="ask", status="ok", result=result)
    transformed = plugin.transform_tool_result("clarify", args, tool_call_id="ask", result=result)
    return json.loads(transformed)["plan_mode_approval"] if transformed else ""


@pytest.fixture(autouse=True)
def no_review_offer(monkeypatch):
    """PR-A's cards are the two-choice form; PR-B's review choice depends on the host approval probe (tests/test_edit_review.py)."""
    monkeypatch.setattr(pm, "_edit_review_available", lambda: False)


@pytest.fixture
def card(make_plugin, session_env):
    session_env["HERMES_SESSION_PLATFORM"] = "cli"
    plugin = make_plugin("approval_card")
    path = write_plan(plugin)
    return plugin, path, submit(plugin, path)


def test_card_submission(card):
    plugin, path, result = card
    assert result["clarify"]["choices"] == [APPROVE, KEEP]
    assert "? Full plan: /planmode show [plan-mode " in result["clarify"]["question"]
    assert path.name in result["clarify"]["question"]
    assert "verbatim" in result["message"] and "not a summary" in result["message"]
    assert "If clarify is unavailable, ask the user to run /planmode approve." in result["message"]
    assert state(plugin)["submission"]["status"] == "awaiting"


@pytest.mark.parametrize("platform,hook,cron", [
    ("", "acp", False), ("", "cron", False), ("", "", True),
    ("api_server", "", False), ("webhook", "", False), ("msgraph_webhook", "", False),
])
def test_unattended_directive_matches_off(make_plugin, session_env, monkeypatch, platform, hook, cron):
    session_env["HERMES_SESSION_PLATFORM"] = platform
    if cron:
        session_env["HERMES_CRON_SESSION"] = "1"
    plugin = make_plugin("approval_card")
    path = write_plan(plugin)
    plugin.pre_llm_call(platform=hook)
    monkeypatch.setattr(pm, "make_rule_key", lambda *args: "plan-mode:fixed")
    before = copy.deepcopy(state(plugin))
    on = plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
    assert on["action"] == "approve"
    plugin._ledger.take(on["rule_key"])
    plugin._save_state("sk:unit-session", before)
    plugin.ctx.settings["levers"] = ""
    off = plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
    assert on == off


@pytest.mark.parametrize("wrong", ["choices", "extra", "multi_select", "question"])
def test_wrong_args_blocked(card, wrong):
    plugin, _, result = card
    args = ask_args(result)
    if wrong == "choices":
        args["questions"][0]["choices"] = [KEEP, APPROVE]
    elif wrong == "extra":
        args["questions"].append({"question": "Anything else?", "choices": ["Yes", "No"]})
    elif wrong == "multi_select":
        args["questions"][0]["multi_select"] = True
    else:
        args["questions"][0]["question"] += " extra"
    assert plugin.pre_tool_call("clarify", args, tool_call_id="ask")["action"] == "block"


def test_recommended_approval(card):
    plugin, _, result = card
    answer_card(plugin, result, APPROVE + " (Recommended)")
    assert state(plugin)["phase"] == "executing"
    assert state(plugin)["submission"]["approved_via"] == "card"
    assert "card_baseline" not in state(plugin)
    assert not plugin._open_asks


def test_changed_digest_never_approved(card):
    plugin, path, result = card
    path.write_text("# Changed\n")
    answer_card(plugin, result)
    assert state(plugin)["active"]
    assert state(plugin)["submission"]["status"] == "stale"
    assert not plugin._open_asks


@pytest.mark.parametrize("outcome", ["cancelled", "timed_out", "undelivered"])
def test_outcome_overrides_partial_approval_and_allows_reask(card, outcome):
    plugin, _, result = card
    note = answer_card(plugin, result, outcome=outcome)
    assert state(plugin)["active"] and state(plugin)["submission"]["status"] == "awaiting"
    assert "Feedback:" not in state(plugin).get("pending_note", "") + note
    assert "did not answer" in note and "stop" in note.lower() and "wait" in note.lower()
    assert not plugin._open_asks
    assert plugin.pre_tool_call("clarify", ask_args(result), tool_call_id="fresh") is None
    assert state(plugin)["submission"]["clarify"]["tool_call_id"] == "fresh"


@pytest.mark.parametrize("response", [{"status": "skipped"}, {"user_response": None}])
def test_no_answer_is_not_feedback(card, response):
    plugin, _, result = card
    note = answer_card(plugin, result, **response)
    assert "did not answer" in note and "Feedback:" not in note
    assert state(plugin)["submission"]["status"] == "awaiting"
    assert plugin.pre_tool_call("clarify", ask_args(result), tool_call_id="fresh") is None


def test_keep_planning(card):
    plugin, _, result = card
    answer_card(plugin, result, KEEP)
    assert state(plugin)["active"] and state(plugin)["submission"]["status"] == "rejected"
    assert "ask what to change" in state(plugin)["pending_note"]
    assert "Feedback:" not in state(plugin)["pending_note"]
    assert not plugin._open_asks


@pytest.mark.parametrize("length", [1500, 2500])
def test_feedback_cap(card, length):
    plugin, _, result = card
    answer_card(plugin, result, "x" * length)
    feedback = state(plugin)["pending_note"].split('Feedback: "')[1].removesuffix('"')
    assert feedback == "x" * min(length, 2000)
    assert state(plugin)["active"] and not plugin._open_asks


def test_live_registry_blocks_second_ask_and_submission(card):
    plugin, path, result = card
    assert plugin.pre_tool_call("clarify", ask_args(result), tool_call_id="live") is None
    assert (state(plugin)["submission"]["clarify"]["nonce"], "live") in plugin._open_asks
    assert plugin.pre_tool_call("clarify", ask_args(result), tool_call_id="second")["action"] == "block"
    blocked = plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
    assert blocked["action"] == "block" and "open approval card first" in blocked["message"]
    assert state(plugin)["revision"] == 1


@pytest.mark.parametrize("action", ["reask", "resubmit"])
def test_restart_accepts_dead_ask(card, action):
    old, path, result = card
    assert old.pre_tool_call("clarify", ask_args(result), tool_call_id="dead") is None
    plugin = pm.PlanModePlugin(old.ctx)
    plugin.register()
    assert not plugin._open_asks
    if action == "reask":
        assert plugin.pre_tool_call("clarify", ask_args(result), tool_call_id="new") is None
        assert state(plugin)["submission"]["clarify"]["tool_call_id"] == "new"
        assert plugin._open_asks == {(state(plugin)["submission"]["clarify"]["nonce"], "new")}
    else:
        assert "rev 2" in submit(plugin, path)["clarify"]["question"]


def test_revision_diff(card):
    plugin, path, first = card
    assert "Changed since" not in first["clarify"]["question"]
    path.write_text("# Parser plan\n\n## Steps\n1. Change parser.py\n## Tests\nRun focused tests.\n")
    second = submit(plugin, path)
    submission = state(plugin)["submission"]
    assert "Changed since rev 1: +" in second["clarify"]["question"]
    assert submission["change_summary"] + ". Full plan:" in second["clarify"]["question"]
    assert len(submission["change_summary"]) <= 300
    assert "Steps" in submission["change_summary"] and "Tests" in submission["change_summary"]
    assert 0 < len(submission["diff_excerpt"]) <= 2000
    assert submission["diff_excerpt"] in second["message"]
    assert "@@" in submission["diff_excerpt"]


def test_diff_computes_opcodes_once(card, monkeypatch):
    plugin, path, _ = card
    calls = []
    original = pm.difflib.SequenceMatcher.get_opcodes
    def counted(matcher):
        calls.append(matcher)
        return original(matcher)
    monkeypatch.setattr(pm.difflib.SequenceMatcher, "get_opcodes", counted)
    path.write_text("# Revised\n")
    submit(plugin, path)
    assert len(calls) == 1


def test_diff_excerpt_truncated_at_line_boundary(card):
    plugin, path, _ = card
    path.write_text("# Plan\n" + "new line with enough text\n" * 200)
    submit(plugin, path)
    excerpt = state(plugin)["submission"]["diff_excerpt"]
    assert len(excerpt) <= 2000 and excerpt.endswith("\n…")


@pytest.mark.parametrize("large_first", [False, True])
def test_oversized_baseline_has_no_diff(make_plugin, large_first):
    plugin = make_plugin("approval_card")
    path = write_plan(plugin, "x" * 65537 if large_first else "# Small\n")
    submit(plugin, path)
    path.write_text("# Small\n" if large_first else "x" * 65537)
    second = submit(plugin, path)
    assert "Changed since" not in second["clarify"]["question"]
    assert not state(plugin)["submission"].get("diff_excerpt")
    if not large_first:
        assert "card_baseline" not in state(plugin)


@pytest.mark.parametrize("ending", ["approve", "card", "off", "on", "reset"])
def test_baseline_cleared(card, ending):
    plugin, _, result = card
    key = pm._state_storage_key("sk:unit-session")
    assert key in plugin._card_baselines
    if ending == "reset":
        plugin.on_session_reset()
    elif ending == "card":
        answer_card(plugin, result)
    else:
        plugin.command(ending)
    assert "card_baseline" not in state(plugin)
    assert key not in plugin._card_baselines


def test_baseline_never_saved_or_copied_to_linked_state(card):
    plugin, path, _ = card
    current = state(plugin)
    alias = pm._state_storage_key("sk:linked-session")
    current["command_session_storage_keys"] = [alias]
    plugin._save_state("sk:unit-session", current)
    path.write_text("# Revised\n")
    submit(plugin, path)
    assert all("card_baseline" not in saved for saved in plugin.ctx.state.values.values()
               if isinstance(saved, dict))
    assert plugin._card_baselines[pm._state_storage_key("sk:unit-session")]["text"] == "# Revised\n"


def test_baseline_global_bound(make_plugin, session_env):
    plugin = make_plugin("approval_card")
    for index in range(40):
        session_env["HERMES_SESSION_KEY"] = f"bounded-{index}"
        submit(plugin, write_plan(plugin))
    assert len(plugin._card_baselines) == 32
    assert list(plugin._card_baselines) == [pm._state_storage_key(f"sk:bounded-{i}") for i in range(8, 40)]
    assert all(len(entry["text"].encode("utf-8")) <= 65536 for entry in plugin._card_baselines.values())


@pytest.mark.parametrize("missing", [False, True])
def test_unknown_outcome_and_legacy_missing_outcome(card, missing):
    plugin, _, result = card
    args = ask_args(result)
    assert plugin.pre_tool_call("clarify", args, tool_call_id="outcome") is None
    payload = json.loads(host_result(result, outcome="interrupted"))
    if missing:
        payload.pop("outcome")
    value = json.dumps(payload)
    plugin.post_tool_call("clarify", args, tool_call_id="outcome", status="ok", result=value)
    current = state(plugin)
    assert (current.get("phase") == "executing") is missing
    assert not plugin._open_asks
    if not missing:
        assert current["active"] and current["submission"]["status"] == "awaiting"
        note = json.loads(plugin.transform_tool_result("clarify", args, tool_call_id="outcome", result=value))
        assert "did not answer" in note["plan_mode_approval"]
        assert plugin.pre_tool_call("clarify", args, tool_call_id="fresh") is None


@pytest.mark.parametrize("short", [False, True])
def test_awaiting_reminder_preserves_long_card(card, short):
    plugin, _, result = card
    current = state(plugin)
    question = "q" * 292
    current["submission"]["clarify"]["question"] = question
    current["light_note_seen"] = short
    plugin.ctx.settings["levers"] = "approval_card light_notes"
    plugin._save_state("sk:unit-session", current)
    note = plugin.pre_llm_call()["context"]
    assert f"question={question}, choices={result['clarify']['choices']}," in note


@pytest.mark.parametrize("large_first", [False, True])
def test_long_plan_skips_diff_but_updates_baseline(make_plugin, large_first):
    plugin = make_plugin("approval_card")
    path = write_plan(plugin, "a\n" * 3000 if large_first else "# Small\n")
    submit(plugin, path)
    text = "# Small\n" if large_first else "b\n" * 3000
    path.write_text(text)
    second = submit(plugin, path)
    assert "Changed since" not in second["clarify"]["question"]
    assert not state(plugin)["submission"].get("diff_excerpt")
    assert plugin._card_baselines[pm._state_storage_key("sk:unit-session")]["text"] == text


def test_reused_call_id_in_other_session_does_not_block_reask(card, session_env):
    plugin, _, first = card
    assert plugin.pre_tool_call("clarify", ask_args(first), tool_call_id="call_0") is None
    session_env["HERMES_SESSION_KEY"] = "second-session"
    second = submit(plugin, write_plan(plugin))
    # A persisted ask from an earlier process, while A still has a live call_0.
    current = plugin._load_state("sk:second-session")
    current["submission"]["clarify"]["tool_call_id"] = "call_0"
    plugin._save_state("sk:second-session", current)
    assert plugin.pre_tool_call("clarify", ask_args(second), tool_call_id="fresh") is None
    nonce = current["submission"]["clarify"]["nonce"]
    assert (nonce, "fresh") in plugin._open_asks


@pytest.mark.parametrize("ending", ["off", "resubmit"])
def test_forget_submission_discards_open_ask(card, ending):
    plugin, path, result = card
    assert plugin.pre_tool_call("clarify", ask_args(result), tool_call_id="open") is None
    assert plugin._open_asks
    if ending == "off":
        plugin.command("off")
    else:
        # Exercise replacement admission directly: public resubmit correctly blocks a live ask.
        assert plugin._prepare_submission("sk:unit-session", state(plugin), {"path": str(path)}, "replace") is None
    assert not plugin._open_asks


def test_clarify_error_discards_id_and_allows_reask(card):
    plugin, _, result = card
    args = ask_args(result)
    assert plugin.pre_tool_call("clarify", args, tool_call_id="failed") is None
    plugin.post_tool_call("clarify", args, tool_call_id="failed", status="error", result=host_result(result))
    current = state(plugin)
    assert current["active"] and current["submission"]["status"] == "awaiting"
    assert not plugin._open_asks and not current["submission"]["clarify"]["tool_call_id"]
    assert plugin.pre_tool_call("clarify", args, tool_call_id="fresh") is None


@pytest.mark.parametrize("size", [10000, 100000])
def test_show_card_plan(make_plugin, size):
    plugin = make_plugin("approval_card")
    text = "x" * size
    path = write_plan(plugin, text)
    shown = plugin.command("show")
    if size <= 65536:
        assert shown.endswith(text) and "truncated" not in shown
    else:
        assert shown.split("\n\n", 1)[1].startswith("x" * 65536)
        assert f"…(truncated: showing 65536 of {size} bytes; full file: {path})" in shown


def test_show_off_unchanged(make_plugin):
    plugin = make_plugin()
    path = write_plan(plugin, "x" * 10000)
    shown = plugin.command("show")
    assert shown == render.plan_text(str(path), "x" * 3501, 0, "planning")
    assert len(shown) == 3500


@pytest.mark.parametrize("enabled", [False, True])
def test_mattermost_plan_once_note(make_plugin, session_env, enabled):
    session_env["HERMES_SESSION_PLATFORM"] = "mattermost"
    plugin = make_plugin("plan_once approval_card" if enabled else "plan_once")
    plugin.command("on")
    note = plugin.pre_llm_call()["context"]
    assert ("3–5 line summary plus the path" in note) is not enabled
    if enabled:
        assert "complete plan file verbatim" in note and "never a summary" in note


@pytest.mark.parametrize("action", ["approve", "reject change it"])
def test_typed_decision_ignores_late_result(card, action):
    plugin, _, result = card
    args = ask_args(result)
    assert plugin.pre_tool_call("clarify", args, tool_call_id="ask") is None
    plugin.command(action)
    before = copy.deepcopy(state(plugin))
    assert not plugin._open_asks
    value = host_result(result)
    plugin.post_tool_call("clarify", args, tool_call_id="ask", status="ok", result=value)
    assert state(plugin) == before
    assert plugin.transform_tool_result("clarify", args, tool_call_id="ask", result=value) is None
    assert (state(plugin).get("phase") == "executing") is action.startswith("approve")


def test_lever_registration(make_plugin):
    assert "approval_card" in pm._LEVERS
    assert "approval_card" not in pm.LEAN_LEVERS
    assert "approval_card" in make_plugin("all")._levers()
    assert pm._NO_CLARIFY_PLATFORMS == NO_CLARIFY


def test_off_card_result_while_directive_pending_matches_base(make_plugin):
    plugin = make_plugin()
    path = write_plan(plugin)
    assert plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)["action"] == "approve"
    card = {"clarify": {"question": "Approve plan rev 1? Full plan: /planmode show [plan-mode fake]",
                        "choices": [APPROVE, KEEP]}}
    before = copy.deepcopy(state(plugin))
    value = host_result(card)
    plugin.post_tool_call("clarify", ask_args(card), tool_call_id="ask", status="ok", result=value)
    assert state(plugin) == before
    assert plugin.transform_tool_result("clarify", ask_args(card), tool_call_id="ask", result=value) is None


@pytest.mark.parametrize("mismatch", ["call_id", "question", "choices", "activation"])
def test_correlation_still_required(card, mismatch):
    plugin, _, result = card
    args = ask_args(result)
    assert plugin.pre_tool_call("clarify", args, tool_call_id="ask") is None
    changes = {}
    if mismatch == "activation":
        current = state(plugin)
        current["activation_id"] = "different"
        plugin._save_state("sk:unit-session", current)
    elif mismatch == "question":
        changes["question"] = "different"
    elif mismatch == "choices":
        changes["choices_offered"] = [KEEP, APPROVE]
    plugin.post_tool_call("clarify", args, tool_call_id="wrong" if mismatch == "call_id" else "ask",
                          status="ok", result=host_result(result, **changes))
    assert state(plugin)["active"] and state(plugin).get("phase") != "executing"


def test_heading_scan_is_linear_on_long_lines():
    import time
    line = "# a" + " " * 3000 + "b"
    start = time.monotonic()
    summary, _ = pm.PlanModePlugin._card_diff({"text": line + "\nold\n", "revision": 1}, line + "\nnew\n")
    assert time.monotonic() - start < 1.0
    assert summary.startswith("Changed since rev 1: +1/−1 lines in a")
    closed, _ = pm.PlanModePlugin._card_diff({"text": "## Tests ##\nold\n", "revision": 2}, "## Tests ##\nnew\n")
    assert closed.endswith(" in Tests")


def test_hook_platform_refresh_keeps_recent_session(make_plugin, session_env):
    session_env["HERMES_SESSION_PLATFORM"] = ""
    plugin = make_plugin("approval_card")
    path = write_plan(plugin)
    plugin.pre_llm_call(platform="acp")
    key = next(reversed(plugin._hook_platforms))
    for index in range(1023):
        plugin._hook_platforms[f"other-{index}"] = ""
    plugin.pre_llm_call(platform="acp")
    plugin._hook_platforms["newest"] = ""
    while len(plugin._hook_platforms) > 1024:
        plugin._hook_platforms.popitem(last=False)
    assert plugin._hook_platforms.get(key) == "acp"
    assert plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)["action"] == "approve"


@pytest.mark.parametrize("hook,card_note", [("acp", False), ("cli", True)])
def test_agent_entered_note_follows_session_platform(make_plugin, session_env, hook, card_note):
    session_env["HERMES_SESSION_PLATFORM"] = ""
    plugin = make_plugin("approval_card")
    plugin.pre_llm_call(platform=hook)
    message = json.loads(plugin.tool({"action": "on", "task": "parser"}))["message"]
    assert ("verbatim, never a summary" in message) is card_note
