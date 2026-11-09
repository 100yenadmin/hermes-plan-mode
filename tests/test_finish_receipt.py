"""1.4.0: a finish line when an approved plan ends, /planmode list, and the agent's inspect and list actions."""
import json

import pytest

from plan_mode import render
from test_approval_card import APPROVE, answer_card, card, state, submit  # noqa: F401 (fixture)
from test_levers import make_plugin, write_plan  # noqa: F401 (fixture)
from test_plan_mode import session_env  # noqa: F401 (fixture)

DONE = [{"id": "1", "content": "work", "status": "completed"}]


def wrote(plugin, path="parser.py", tool="write_file"):
    args = {"path": path, "content": "x"} if tool == "write_file" else {"path": path, "old_string": "a", "new_string": "b"}
    plugin.post_tool_call(tool, args, tool_call_id=f"w-{path}", turn_id="t1", status="ok", result="{}")


def finish(plugin, todos=DONE):
    plugin.post_tool_call("todo_list", {"todos": todos}, turn_id="t1", status="ok", result=json.dumps({"todos": todos}))


@pytest.fixture
def building(card):
    plugin, path, result = card
    plugin.ctx.settings["levers"] = "approval_card todo_inline"
    answer_card(plugin, result, APPROVE)
    assert state(plugin)["phase"] == "executing"
    return plugin, path


def test_finish_line_names_steps_and_files_on_local_surfaces(building):
    plugin, _ = building
    wrote(plugin, "parser.py")
    wrote(plugin, "parser.py", tool="patch")  # the same file counts once
    wrote(plugin, "README.md", tool="patch")
    finish(plugin)
    reply = plugin.transform_llm_output("All tests pass.", platform="cli", turn_id="t1")
    assert reply == "All tests pass.\n\n✅ Plan rev 1 done: 1 of 1 steps, 2 files changed (parser.py, README.md)."
    plugin.post_llm_call()
    assert "phase" not in state(plugin)
    assert plugin.transform_llm_output("Next.", platform="cli", turn_id="t2") is None  # shown once


def test_chat_finish_line_counts_only_and_replaces_the_progress_footer(building):
    plugin, _ = building
    wrote(plugin, "secret-client-notes.md")
    finish(plugin)
    reply = plugin.transform_llm_output("Done.", platform="telegram", turn_id="t1")
    assert reply == "Done.\n\n✅ Plan rev 1 done: 1 of 1 steps, 1 file changed."
    assert "Plan progress" not in reply and "secret" not in reply


@pytest.mark.parametrize("platform", ["api_server", "webhook", "cron"])
def test_programs_get_no_finish_line(building, platform):
    plugin, _ = building
    finish(plugin)
    assert "✅" not in (plugin.transform_llm_output("Done.", platform=platform, turn_id="t1") or "")


def test_footer_off_turns_the_finish_line_off(building):
    plugin, _ = building
    plugin.ctx.settings["footer"] = "off"
    finish(plugin)
    assert plugin.transform_llm_output("Done.", platform="cli", turn_id="t1") is None


def test_short_plan_with_changes_gets_a_finish_line(building):
    plugin, _ = building
    wrote(plugin, "parser.py")
    assert plugin.transform_llm_output("Done.", platform="cli", turn_id="t1").endswith(
        "✅ Plan rev 1 done: 1 file changed (parser.py).")


def test_short_plan_without_changes_gets_none(building):
    plugin, _ = building
    assert plugin.transform_llm_output("I need to ask first.", platform="cli", turn_id="t1") is None


def test_unfinished_plan_gets_no_finish_line(building):
    plugin, _ = building
    finish(plugin, DONE + [{"id": "2", "content": "docs", "status": "in_progress"}])
    assert "✅" not in (plugin.transform_llm_output("Working.", platform="cli", turn_id="t1") or "")


def test_classic_finish_mid_turn_is_kept_for_the_reply(card):
    plugin, _, result = card  # levers "approval_card": no todo levers, so completion ends the plan at once
    answer_card(plugin, result, APPROVE)
    wrote(plugin, "parser.py")
    finish(plugin)
    assert "phase" not in state(plugin)
    assert plugin.transform_llm_output("Done.", platform="cli", turn_id="t1").endswith(
        "✅ Plan rev 1 done: 1 of 1 steps, 1 file changed (parser.py).")
    assert plugin.transform_llm_output("Again.", platform="cli", turn_id="t1") is None


def test_finish_line_follows_the_block_receipt(building):
    plugin, _ = building
    message = plugin.pre_tool_call("terminal", {"command": "git commit -m x"}, tool_call_id="c1")["message"]
    plugin.post_tool_call("terminal", {"command": "git commit -m x"}, tool_call_id="c1", turn_id="t1",
                          status="blocked", error_message=message, result=json.dumps({"error": message}))
    finish(plugin)
    reply = plugin.transform_llm_output("Done.", platform="cli", turn_id="t1")
    lines = reply.split("\n")
    assert lines[-2].startswith("🔒 Plan mode blocked 1 tool call: terminal") and lines[-1].startswith("✅ Plan rev 1 done")


def test_finish_receipt_render():
    assert render.finish_receipt(2, {"total": 5, "completed": 4, "cancelled": 1}, [], details=True) == \
        "✅ Plan rev 2 done: 4 of 5 steps (1 cancelled)."
    files = ["/a/one.py", "/a/two.py", "/a/three.py", "/a/four.py"]
    assert render.finish_receipt(1, None, files, details=True) == \
        "✅ Plan rev 1 done: 4 files changed (one.py, two.py, three.py and 1 more)."


def test_list_shows_each_plan_with_its_status(card, tmp_path):
    plugin, path, result = card
    draft = path.parent / "2026-11-01_old-idea.md"
    draft.write_text("# old", encoding="utf-8")
    import os
    os.utime(draft, (1, 1))  # oldest
    listing = plugin.command("list")
    assert listing.splitlines()[0] == f"Plans in `{path.parent}`, newest first:"
    assert f"1. `{path.name}`: waiting for approval (rev 1)" in listing
    assert "2. `2026-11-01_old-idea.md`: draft" in listing
    plugin.ctx.settings["levers"] = "approval_card todo_inline"
    answer_card(plugin, result, APPROVE)
    finish(plugin, DONE + [{"id": "2", "content": "b", "status": "in_progress"}])
    assert f"`{path.name}`: building rev 1 (1 of 2 steps)" in plugin.command("list")
    finish(plugin, DONE + [{"id": "2", "content": "b", "status": "completed"}])
    plugin.post_llm_call()
    assert f"`{path.name}`: done (rev 1, " in plugin.command("list")


def test_list_does_not_create_the_plans_directory(make_plugin, session_env, tmp_path):
    plugin = make_plugin("approval_card")
    assert plugin.command("list") == f"No plans yet in `{tmp_path.resolve() / '.hermes' / 'plans'}`."
    assert not (tmp_path / ".hermes").exists()


def test_agent_inspect_returns_fields_without_changing_state(building):
    plugin, path = building
    wrote(plugin, "parser.py")
    before = json.dumps(state(plugin), sort_keys=True)
    fields = json.loads(plugin.tool({"action": "inspect"}))
    assert fields["phase"] == "executing" and fields["plan_mode"] == "off"
    assert fields["plan"] == str(path) and fields["revision"] == 1
    assert fields["edit_policy"] == "auto_accept" and fields["files_changed"] == 1
    assert fields["finishing_this_turn"] is False and fields["next"]
    assert json.dumps(state(plugin), sort_keys=True) == before


def test_agent_inspect_while_planning_and_list(make_plugin, session_env):
    plugin = make_plugin("approval_card")
    path = write_plan(plugin)
    fields = json.loads(plugin.tool({"action": "inspect"}))
    assert fields["phase"] == "planning" and fields["plan_mode"] == "on" and fields["edit_policy"] is None
    listing = json.loads(plugin.tool({"action": "list"}))
    assert listing["plans_dir"] == str(path.parent)
    assert [(item["name"], item["status"]) for item in listing["plans"]] == [(path.name, "being planned")]
