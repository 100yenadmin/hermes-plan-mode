"""The awaiting-approval note tells the agent to wait for a turn the user started."""
from test_approval_card import card, state  # noqa: F401 (fixture)
from test_levers import make_plugin  # noqa: F401 (fixture)
from test_plan_mode import _submit, _submit_result, session_env  # noqa: F401 (fixture)

RULE = "On a turn the user did not write (a notice, a wake or another agent's message), do not ask again: wait for the user."


def test_card_note_keeps_the_exact_question_and_adds_the_rule(card):
    plugin, _, result = card
    assert state(plugin)["submission"]["status"] == "awaiting"
    note = next(line for line in plugin.pre_llm_call()["context"].splitlines() if "awaits approval" in line)
    # The exact question and choices come first, so the card can still be asked word for word.
    assert f"question={result['clarify']['question']}, choices={result['clarify']['choices']}" in note
    assert note.endswith(RULE)


def test_directive_note_keeps_the_rule_after_the_399_character_cut(make_plugin, tmp_path):
    plugin = make_plugin(plan_profile="classic")
    assert "Plan mode is on" in plugin.command("on approval proof")
    long_path = tmp_path / ".hermes" / "plans" / ("x" * 197 + ".md")
    args = {"path": str(long_path), "content": "# Plan\n\n## Implement\n## Verify\n"}
    ids = {"session_id": "s1", "tool_call_id": "write-plan"}
    assert plugin.pre_tool_call("write_file", args, **ids) is None
    long_path.write_text(args["content"], encoding="utf-8")
    plugin.post_tool_call("write_file", args, status="ok", **ids)
    _submit(plugin)
    clarify = _submit_result(plugin)["clarify"]
    prompt = (f"Plan rev 1 awaits approval: ask with clarify using exactly question={clarify['question']}, "
              f"choices={clarify['choices']}, or the user can run /planmode approve.")
    assert len(long_path.name) == 200 and len(prompt) > 399
    note = next(line for line in plugin.pre_llm_call()["context"].splitlines() if "awaits approval" in line)
    assert note == prompt[:399] + " " + RULE
