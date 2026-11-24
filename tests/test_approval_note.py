"""1.6.1: the awaiting-approval turn note does not ask the agent to re-ask on turns the user did not write.

Hermes gives plugins no signal for who started a turn (a background notice, a wake, another agent's message), so the
note states the rule and the model applies it (field report, 2026-11-24).
"""
from test_approval_card import card, state  # noqa: F401 (fixture)
from test_levers import make_plugin  # noqa: F401 (fixture)
from test_plan_mode import session_env  # noqa: F401 (fixture)

RULE = "On a turn the user did not write (a notice, a wake or another agent's message), do not ask again: wait for the user."


def test_card_note_keeps_the_exact_question_and_adds_the_rule(card):
    plugin, _, result = card
    assert state(plugin)["submission"]["status"] == "awaiting"
    note = next(line for line in plugin.pre_llm_call()["context"].splitlines() if "awaits approval" in line)
    # The exact question and choices come first, so the card can still be asked word for word.
    assert f"question={result['clarify']['question']}, choices={result['clarify']['choices']}" in note
    assert note.endswith(RULE)
