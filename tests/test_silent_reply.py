"""1.6.1: a reply that is only a silence marker stays exactly as written, so the host keeps it silent.

Hermes runs transform_llm_output before its gateway decides whether a reply is silent. Anything plan mode appends to
``[SILENT]`` turns it into a message the user receives (field report, 2026-11-24).
"""
import json
import sys

import pytest

from plan_mode import plugin as pm
from plan_mode import render
from test_approval_pointer import awaiting, expected  # noqa: F401 (fixture)
from test_block_receipt import blocked, planning  # noqa: F401 (fixture)
from test_levers import make_plugin  # noqa: F401 (fixture)
from test_plan_mode import executing_plan, plan_profile, plugin, session_env, submitted_plan  # noqa: F401 (fixtures)

pytestmark = pytest.mark.usefixtures("directive_lean")  # the executing fixtures approve by directive


@pytest.fixture(autouse=True)
def plain_cli_process(monkeypatch):
    # test_real_plugin_manager runs real gateway code in this process, which would mark it as a server for later tests.
    monkeypatch.setattr(pm, "_session_context_is_engaged", lambda: False)
    monkeypatch.setattr(pm, "_gateway_process_is_admitted", lambda: False)

# What the gateway treats as silence: the whole reply is a marker (case, spacing and edge punctuation ignored), or a
# marker closes the reply on its own line. Cron and webhook lanes also accept a bracketed marker that opens the reply.
SILENT = [
    "[SILENT]", " [silent] \n", "SILENT", "NO_REPLY", "no reply", "NO   REPLY", "[SILENT].", "*NO_REPLY*", "„NO_REPLY“",
    "[静默]", "静默", "[沉默]", "沉默。",
    "Answer.\n\nNO_REPLY", "Answer.\n\n[SILENT]\n", "[SILENT] nothing new this tick", "[SILENT]\nnothing new",
    "", " ", "\n\n",
]
SPOKEN = [
    "Working", "No reply needed from you: I fixed it.", "Silent retry succeeded.", "The NO_REPLY marker is mid-sentence.",
    "SILENTLY", "[SILENT", "x" * 70 + " NO_REPLY",
]


def progress(plugin, current="Level 1 build"):
    todos = [{"status": "completed", "content": "Read"}, {"status": "in_progress", "content": current},
             {"status": "pending", "content": "Ship"}]
    plugin.post_tool_call("todo_list", {"todos": []}, status="ok", result=json.dumps({"todos": todos}))


@pytest.mark.parametrize("reply", SILENT)
def test_a_silent_reply_gets_no_footer_while_executing(plugin, executing_plan, reply):
    progress(plugin)
    footer = plugin.transform_llm_output(response_text="Working", platform="telegram")
    assert footer == "Working\n\nPlan progress 1/3 · now: Level 1 build"  # the same state does get the footer
    assert plugin.transform_llm_output(response_text=reply, platform="telegram") is None


@pytest.mark.parametrize("reply", SPOKEN)
def test_a_reply_that_only_mentions_a_marker_keeps_the_footer(plugin, executing_plan, reply):
    progress(plugin)
    footer = plugin.transform_llm_output(response_text=reply, platform="telegram")
    assert footer == reply.rstrip() + "\n\nPlan progress 1/3 · now: Level 1 build"


@pytest.mark.parametrize("reply", ["[SILENT]", "NO_REPLY", "Saved.\n\n[SILENT]"])
def test_a_silent_reply_gets_no_planning_footer(plugin, submitted_plan, reply):
    assert plugin.transform_llm_output(response_text="Draft", platform="telegram").endswith("until you approve the plan.")
    assert plugin.transform_llm_output(response_text=reply, platform="telegram") is None


def test_a_silent_reply_gets_no_approval_pointer(awaiting):
    plugin, _, _, _, platform = awaiting
    assert plugin.transform_llm_output("Saved the plan.", platform=platform) == expected("Saved the plan.")
    assert plugin.transform_llm_output("[SILENT]", platform=platform) is None


def test_a_silent_reply_drops_the_block_receipt_like_footer_off(planning):
    blocked(planning, "terminal", {"command": "ls"}, platform="cli")
    assert planning.transform_llm_output("[SILENT]", platform="cli") is None
    # Consumed, not carried: a later reply never names an earlier turn's blocks.
    assert planning.transform_llm_output("Here is the plan.", platform="cli") is None


def test_a_plan_that_finishes_on_a_silent_turn_shows_no_done_line(plugin, executing_plan):
    plugin.ctx.settings["levers"] = "approval_card todo_inline"
    plugin.post_tool_call("todo_list", {"todos": []}, status="ok", result=json.dumps({"todos": [
        {"status": "completed", "content": "Build"}]}))
    assert plugin.transform_llm_output(response_text="[SILENT]", platform="telegram") is None
    plugin.post_llm_call(platform="telegram")
    assert plugin._load_state("sk:unit-session").get("phase") != "executing"  # the plan still finishes


@pytest.mark.parametrize("reply", SILENT)
def test_local_rule_finds_every_silent_form(reply):
    assert render.is_silent_reply(reply)


@pytest.mark.parametrize("reply", SPOKEN)
def test_local_rule_keeps_spoken_replies(reply):
    assert not render.is_silent_reply(reply)


@pytest.mark.parametrize("value", [None, 7, b"[SILENT]"])
def test_non_text_counts_as_silent(value):
    assert render.is_silent_reply(value)  # never append to something that is not text


def _host_filters():
    return pytest.importorskip("gateway.response_filters")  # Hermes legs only


def test_local_markers_cover_the_host():
    host = _host_filters()
    markers = getattr(host, "LIVE_GATEWAY_SILENT_MARKERS", None)
    if markers is None:
        pytest.skip("this Hermes has no shared marker set")
    # Every marker the host keeps silent must be known here; older hosts know fewer (no zh forms), which only means
    # a reply such as "静默" loses its footer there, never that a silent turn leaks.
    assert markers <= render.SILENCE_MARKERS


@pytest.mark.parametrize("reply", SILENT + SPOKEN)
def test_untouched_or_footed_the_host_delivers_the_same_text(plugin, executing_plan, reply):
    host = _host_filters()
    progress(plugin)
    out = plugin.transform_llm_output(response_text=reply, platform="telegram") or reply
    assert host.is_intentional_silence_response(out) == host.is_intentional_silence_response(reply)
    if reply.strip():
        # What the user would read. A footed reply adds only the footer line. Older hosts have no trailing rule.
        strip = getattr(host, "strip_trailing_silence_marker", lambda text: text)
        delivered, before = strip(out), strip(reply)
        assert delivered == before or delivered == before.rstrip() + "\n\nPlan progress 1/3 · now: Level 1 build"


@pytest.mark.parametrize("reply", SILENT)
def test_the_local_rule_holds_when_the_host_module_is_absent(plugin, executing_plan, monkeypatch, reply):
    monkeypatch.setitem(sys.modules, "gateway.response_filters", None)
    progress(plugin)
    assert plugin.transform_llm_output(response_text=reply, platform="telegram") is None


def test_a_broken_host_helper_falls_back_to_the_local_rule(plugin, executing_plan, monkeypatch):
    class Broken:
        def is_autonomous_silence_response(self, _):
            raise RuntimeError("boom")

    monkeypatch.setitem(sys.modules, "gateway.response_filters", Broken())
    progress(plugin)
    assert plugin.transform_llm_output(response_text="[SILENT]", platform="telegram") is None


def test_a_host_marker_the_plugin_does_not_know_is_still_silent(plugin, executing_plan, monkeypatch):
    class Newer:
        def is_autonomous_silence_response(self, text):
            return text.strip() == "[QUIET]"

    monkeypatch.setitem(sys.modules, "gateway.response_filters", Newer())
    progress(plugin)
    assert plugin.transform_llm_output(response_text="[QUIET]", platform="telegram") is None


# The footer cut the step name mid-word.
@pytest.mark.parametrize("content, current", [
    ("Build", "Build"),
    ("x" * 60, "x" * 60),
    ("Step 2 build: write the parser module and its tests, then run the full harness",
     "Step 2 build: write the parser module and its tests, then…"),
    ("y" * 70, "y" * 59 + "…"),
    ("a " + "z" * 70, "a " + "z" * 57 + "…"),  # a cut that would keep almost nothing falls back to a hard cut
])
def test_the_step_name_is_cut_at_a_word(content, current):
    progress = render.todo_progress(json.dumps({"todos": [{"status": "in_progress", "content": content}]}))
    assert progress["current"] == current and len(progress["current"]) <= 60
