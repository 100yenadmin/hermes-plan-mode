"""`/planmode on <task>`: the task reaches the model on the next turn, once."""
from test_plan_mode import _BUILTIN_PROMPT, plugin, plan_profile, session_env

QUEUED = "The user's task for this plan (from /planmode on):"


def _context(plugin, message="Plan it."):
    return (plugin.pre_llm_call(user_message=message) or {}).get("context", "")


def test_slash_task_reaches_the_next_turn_once(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on Add a subtract(a, b) function")
    assert f"{QUEUED} Add a subtract(a, b) function" in _context(plugin)
    assert QUEUED not in _context(plugin)


def test_slash_without_task_queues_nothing(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on")
    assert QUEUED not in _context(plugin)


def test_slash_task_is_bounded(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.command("on " + "x" * 5000)
    context = _context(plugin)
    assert f"{QUEUED} {'x' * 2000}" in context and "x" * 2001 not in context


def test_builtin_plan_does_not_repeat_its_task(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    assert QUEUED not in _context(plugin, _BUILTIN_PROMPT)


def test_agent_activation_queues_no_task(plugin, session_env, tmp_path):
    session_env["TERMINAL_CWD"] = str(tmp_path)
    plugin.ctx.tools["plan_mode"]["handler"]({"action": "on", "reason": "multi-file change"}, session_id="s1")
    assert QUEUED not in _context(plugin)


def test_tui_cold_resume_does_not_replay_the_task(plugin, session_env, tmp_path):
    # TUI/Desktop slash commands run without the UI id; the first turn adopts that sk: copy into the tab's state.
    session_env.update({"HERMES_SESSION_KEY": "resume-key", "HERMES_SESSION_SOURCE": "tui",
                        "HERMES_UI_SESSION_ID": "", "TERMINAL_CWD": str(tmp_path)})
    plugin.command("on unique-reopen-task")
    session_env["HERMES_UI_SESSION_ID"] = "tab-1"
    assert _context(plugin).count("unique-reopen-task") == 1
    assert "unique-reopen-task" not in _context(plugin)
    session_env["HERMES_UI_SESSION_ID"] = "reopened-tab"  # cold resume: a fresh UI id on the same session key
    assert "unique-reopen-task" not in _context(plugin)


def test_tui_cold_resume_does_not_replay_a_slash_reject_note(plugin, session_env, tmp_path):
    session_env.update({"HERMES_SESSION_KEY": "resume-key", "HERMES_SESSION_SOURCE": "tui",
                        "HERMES_UI_SESSION_ID": "", "TERMINAL_CWD": str(tmp_path)})
    plugin.command("on")
    session_env["HERMES_UI_SESSION_ID"] = "tab-1"
    _context(plugin)
    session_env["HERMES_UI_SESSION_ID"] = ""
    assert "remains on" in plugin.command("reject unique-revision-ask")
    session_env["HERMES_UI_SESSION_ID"] = "tab-1"
    assert _context(plugin).count("unique-revision-ask") == 1
    session_env["HERMES_UI_SESSION_ID"] = "reopened-tab"
    assert "unique-revision-ask" not in _context(plugin)
