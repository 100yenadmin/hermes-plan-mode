"""PR-B D4 decisions: correlated approval, persistent review and fail-open edits."""
import copy
import json
import sys
from types import ModuleType

import pytest

from plan_mode import plugin as pm
from test_approval_card import APPROVE, KEEP, answer_card, ask_args, card, state, submit
from test_levers import IDS, make_plugin, write_plan
from test_plan_mode import session_env


REVIEW = "Approve, review each file edit"
PAUSED = "Edit review is paused: Hermes approvals are off (yolo or approvals.mode off), so edits apply without a prompt."
DENIED = "The user denied an edit; ask what to change before retrying it."
EDITS = [
    ("write_file", {"path": "parser.py", "content": "first\nsecond\n"}),
    ("patch", {"path": "parser.py", "old_string": "before", "new_string": "after"}),
    ("patch", {"mode": "patch", "patch": "*** Begin Patch\n*** Update File: parser.py\n@@\n-before\n+after\n*** Add File: helper.py\n+new\n*** End Patch"}),
]


@pytest.mark.parametrize("platform", ["cli", "", "telegram"])
@pytest.mark.parametrize("ui", [False, True])
@pytest.mark.parametrize("tool,args,lines,count", [
    (*EDITS[0], ["+first", "+second"], "(2 lines)"),
    ("patch", {"path": "parser.py", "old_string": "before\nold", "new_string": "after\nnew"},
     ["-before", "-old", "+after", "+new"], "(+2/−2 lines)"),
    (*EDITS[2], ["Update File: parser.py", "-before", "+after", "Add File: helper.py", "+new"], "(+2/−1 lines)"),
])
def test_changed_line_preview(reviewed, session_env, platform, ui, tool, args, lines, count):
    plugin, _ = reviewed
    session_env["HERMES_SESSION_PLATFORM"] = platform
    if ui:
        plugin._save_state("ui:preview-tab", state(plugin))
        session_env["HERMES_UI_SESSION_ID"] = "preview-tab"
    text = plugin.pre_tool_call(tool, args)["message"]
    assert count in text
    if not ui and platform in {"cli", ""}:
        assert "\n" not in text
        assert text.endswith(" — " + " ⏎ ".join(lines))
    else:
        assert text.splitlines()[1:] == lines


@pytest.mark.parametrize("runtime_cwd", [False, True])
@pytest.mark.parametrize("outside", [False, True])
def test_preview_session_relative_path(reviewed, session_env, monkeypatch, tmp_path, runtime_cwd, outside):
    plugin, _ = reviewed
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    session_env["TERMINAL_CWD"] = str(cwd)
    if runtime_cwd:
        session_env["TERMINAL_CWD"] = str(tmp_path)
        monkeypatch.setattr(pm, "_runtime_cwd_reader", lambda: lambda: cwd)
    target = (tmp_path if outside else cwd) / "parser.py"
    text = plugin.pre_tool_call("write_file", {"path": str(target), "content": "first"})["message"]
    expected = f"{target} (1 line, 1 path outside the working directory)" if outside else "parser.py (1 line)"
    assert f"write_file {expected}" in text


def test_preview_counts_whole_edit_and_shows_eight_lines(reviewed, session_env):
    plugin, _ = reviewed
    plugin._save_state("ui:preview-tab", state(plugin))
    session_env["HERMES_UI_SESSION_ID"] = "preview-tab"
    text = plugin.pre_tool_call("write_file", {"path": "parser.py", "content": "\n".join(map(str, range(12)))})["message"]
    assert "(12 lines)" in text
    assert text.splitlines()[1:] == [f"+{n}" for n in range(8)] + ["… 4 more lines"]


@pytest.fixture(autouse=True)
def probe(monkeypatch):
    """A host seam without config, auth, or approval-policy reads."""
    module = ModuleType("tools.approval")
    module.is_approval_bypass_active = lambda: False
    monkeypatch.setitem(sys.modules, "tools.approval", module)
    return module


def set_probe(module, value):
    if value == "missing":
        del module.is_approval_bypass_active
    elif value == "raising":
        def raises():
            raise RuntimeError("probe unavailable")
        module.is_approval_bypass_active = raises
    elif value == "noncallable":
        module.is_approval_bypass_active = False
    else:
        module.is_approval_bypass_active = lambda: value


@pytest.fixture
def reviewed(card):
    plugin, path, result = card
    answer_card(plugin, result, REVIEW)
    return plugin, path


def note(plugin):
    return (plugin.pre_llm_call() or {}).get("context", "")


def test_offer(card):
    plugin, _, result = card
    assert result["clarify"]["choices"] == [APPROVE, REVIEW, KEEP]
    assert state(plugin)["submission"]["clarify"]["choices"] == result["clarify"]["choices"]
    assert str(result["clarify"]["choices"]) in result["message"]


@pytest.mark.parametrize("value", [True, "missing", "raising", "noncallable", 0, None])
def test_no_offer_without_exact_false(make_plugin, probe, value):
    set_probe(probe, value)
    plugin = make_plugin("approval_card")
    assert submit(plugin, write_plan(plugin))["clarify"]["choices"] == [APPROVE, KEEP]


@pytest.mark.parametrize("platform,hook,cron", [("acp", "", False), ("", "cron", False), ("", "acp", False), ("", "", True)])
def test_no_clarify_uses_directive(make_plugin, session_env, platform, hook, cron):
    session_env["HERMES_SESSION_PLATFORM"] = platform
    if cron:
        session_env["HERMES_CRON_SESSION"] = "1"
    plugin = make_plugin("approval_card")
    path = write_plan(plugin)
    plugin.pre_llm_call(platform=hook)
    assert plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)["action"] == "approve"


def test_choice_two_approves_and_fresh_keys(card):
    plugin, path, result = card
    answer_card(plugin, result, REVIEW)
    current = state(plugin)
    assert current["phase"] == "executing" and not current["active"]
    assert current["submission"]["approved_via"] == "card"
    review = current["edit_review"]
    assert review["key"].startswith("plan-mode-edit:")
    assert len(review["key"].removeprefix("plan-mode-edit:")) == 16
    assert review["path"] == str(path) and review["rev"] == 1
    plugin.command("on")
    result = submit(plugin, path)
    answer_card(plugin, result, REVIEW + " (Recommended)")
    assert state(plugin)["edit_review"]["key"] != review["key"]


def test_three_choice_keep_is_not_feedback(card):
    plugin, _, result = card
    answer_card(plugin, result, KEEP)
    assert state(plugin)["active"] and state(plugin)["submission"]["status"] == "rejected"
    assert "Feedback:" not in state(plugin)["pending_note"]
    assert "edit_review" not in state(plugin)


@pytest.mark.parametrize("invalid", ["digest", "activation", "cancelled", "timed_out", "undelivered"])
def test_review_uses_same_approval_checks(card, invalid):
    plugin, path, result = card
    if invalid == "digest":
        path.write_text("# Changed\n")
    if invalid == "activation":
        current = state(plugin)
        current["activation_id"] = "different"
        plugin._save_state("sk:unit-session", current)
    answer_card(plugin, result, REVIEW, outcome=invalid if invalid not in {"digest", "activation"} else "submitted")
    assert state(plugin)["active"] and "edit_review" not in state(plugin)


def test_exact_stored_three_choice_args(card):
    plugin, _, result = card
    wrong = ask_args(result)
    wrong["questions"][0]["choices"] = [APPROVE, KEEP]
    assert plugin.pre_tool_call("clarify", wrong, tool_call_id="bad")["action"] == "block"


@pytest.mark.parametrize("tool,args", EDITS)
@pytest.mark.parametrize("inner", [False, True])
def test_file_edit_gate(reviewed, tool, args, inner):
    plugin, _ = reviewed
    directive = plugin.pre_tool_call(tool, args, **({} if inner else IDS))
    assert directive["action"] == "approve"
    assert directive["rule_key"] == state(plugin)["edit_review"]["key"]
    assert "Plan-mode edit review (plan rev 1): " + tool in directive["message"]
    assert "parser.py" in directive["message"]
    if "helper.py" in str(args):
        assert "helper.py" in directive["message"]
    assert ("first" if tool == "write_file" else "before") in directive["message"]
    if tool == "patch":
        assert "after" in directive["message"]


@pytest.mark.parametrize("tool", ["terminal", "read_file", "todo", "skill_view", "execute_code", "delegate_task", "mcp_write"])
def test_no_other_tool_gate(reviewed, tool):
    plugin, _ = reviewed
    assert plugin.pre_tool_call(tool, {"name": "project-tool"}) is None


def test_exec_skill_guard_still_applies(reviewed):
    plugin, _ = reviewed
    plugin.ctx.settings["levers"] = "approval_card exec_skill_guard"
    assert plugin.pre_tool_call("skill_view", {"name": "writing-plans"})["action"] == "block"


@pytest.mark.parametrize("tool,args", EDITS)
def test_active_planning_is_unchanged(make_plugin, tool, args):
    plugin = make_plugin("approval_card")
    write_plan(plugin)
    current = state(plugin)
    before = plugin.pre_tool_call(tool, args)
    assert before["action"] == "block"
    current["edit_review"] = {"key": "plan-mode-edit:stale", "path": "plan.md", "rev": 1}
    plugin._save_state("sk:unit-session", current)
    assert plugin.pre_tool_call(tool, args) == before


@pytest.mark.parametrize("lever", ["todo_batch", "todo_inline"])
def test_automatic_turn_end_keeps_gate(reviewed, lever):
    plugin, _ = reviewed
    plugin.ctx.settings["levers"] = "approval_card " + lever
    review = copy.deepcopy(state(plugin)["edit_review"])
    plugin.post_llm_call(**IDS)
    assert "phase" not in state(plugin) and state(plugin)["edit_review"] == review
    assert plugin.pre_tool_call(*EDITS[0])["action"] == "approve"


def test_101_turn_cap_keeps_gate(reviewed):
    plugin, _ = reviewed
    review = copy.deepcopy(state(plugin)["edit_review"])
    for _ in range(101):
        plugin.pre_llm_call()
    assert "phase" not in state(plugin) and state(plugin)["edit_review"] == review
    assert plugin.pre_tool_call(*EDITS[0])["action"] == "approve"


@pytest.mark.parametrize("ending", ["done", "off", "todos", "on", "agent_on", "auto", "typed"])
@pytest.mark.parametrize("automatic_clear", [False, True])
def test_explicit_ends_clear_review(reviewed, ending, automatic_clear):
    plugin, path = reviewed
    if automatic_clear:
        plugin.ctx.settings["levers"] = "approval_card todo_batch"
        plugin.post_llm_call()
    if ending == "todos":
        todos = [{"id": "1", "content": "work", "status": "completed"}]
        plugin.post_tool_call("todo", {"todos": todos}, status="ok", result=json.dumps({"todos": todos}))
    elif ending == "agent_on":
        plugin.tool({"action": "on"})
    elif ending in {"auto", "typed"}:
        plugin.command("on")
        result = submit(plugin, path)
        current = state(plugin)
        current["edit_review"] = {"key": "plan-mode-edit:prior", "path": str(path), "rev": 0}
        plugin._save_state("sk:unit-session", current)
        if ending == "auto":
            answer_card(plugin, result, APPROVE)
        else:
            plugin.command("approve")
    else:
        plugin.command(ending)
    assert "edit_review" not in state(plugin)


@pytest.mark.parametrize("value", [True, "missing", "raising", "noncallable"])
@pytest.mark.parametrize("automatic_clear", [False, True])
def test_edit_bypass_paused_once(reviewed, probe, value, automatic_clear):
    plugin, _ = reviewed
    if automatic_clear:
        plugin.ctx.settings["levers"] = "approval_card todo_inline"
        plugin.post_llm_call()
    set_probe(probe, value)
    for _ in range(2):
        assert plugin.pre_tool_call(*EDITS[0]) is None
    assert state(plugin)["edit_review"]["paused"] is True
    assert note(plugin).count(PAUSED) == 1
    assert PAUSED not in note(plugin)
    assert plugin.pre_tool_call(*EDITS[0]) is None
    assert PAUSED not in note(plugin)


@pytest.mark.parametrize("automatic_clear", [False, True])
def test_deny_note_once_and_ledger_unchanged(reviewed, automatic_clear):
    plugin, _ = reviewed
    if automatic_clear:
        plugin.ctx.settings["levers"] = "approval_card todo_batch"
        plugin.post_llm_call()
    callback = plugin.ctx.hooks["post_approval_response"]
    plugin._ledger.mark_inflight("plan-mode:ledger", "ledger-call")
    callback(pattern_key="plugin_rule:plan-mode:ledger", choice="once", tool_call_id="ledger-call")
    assert plugin._ledger.take("plan-mode:ledger")["choice"] == "once"
    key = state(plugin)["edit_review"]["key"]
    callback(pattern_key="plugin_rule:" + key, choice="deny", session_key="unit-session")
    assert state(plugin)["edit_review"]["denied"] is True
    assert note(plugin).count(DENIED) == 1
    assert not state(plugin)["edit_review"].get("denied")
    assert DENIED not in note(plugin)


@pytest.mark.parametrize("pattern,choice", [("plugin_rule:other", "deny"), (None, "deny"), ("matching", "once"), ("matching", "session"), ("matching", "always")])
def test_unrelated_or_allowed_response_no_denial(reviewed, pattern, choice):
    plugin, _ = reviewed
    if pattern == "matching":
        pattern = "plugin_rule:" + state(plugin)["edit_review"]["key"]
    plugin.ctx.hooks["post_approval_response"](pattern_key=pattern, choice=choice)
    assert not state(plugin)["edit_review"].get("denied")
    assert DENIED not in note(plugin)


def test_auto_approval_never_gates(card):
    plugin, _, result = card
    answer_card(plugin, result, APPROVE)
    assert state(plugin)["phase"] == "executing" and "edit_review" not in state(plugin)
    for tool, args in EDITS:
        assert plugin.pre_tool_call(tool, args) is None


@pytest.mark.usefixtures("directive_lean")
def test_lever_off_no_review_behavior(make_plugin):
    plugin = make_plugin()
    path = write_plan(plugin)
    plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)}, **IDS)
    result = json.loads(plugin.tool({"action": "submit"}))
    assert REVIEW not in result["clarify"]["choices"]
    plugin.command("approve")
    current = state(plugin)
    current["edit_review"] = {"key": "plan-mode-edit:old", "path": str(path), "rev": 1}
    plugin._save_state("sk:unit-session", current)
    for tool, args in EDITS:
        assert plugin.pre_tool_call(tool, args) is None
    plugin.ctx.hooks["post_approval_response"](pattern_key="plugin_rule:plan-mode-edit:old", choice="deny")
    assert not state(plugin)["edit_review"].get("denied")


def test_directive_render_failure_is_fail_open(reviewed, monkeypatch):
    plugin, _ = reviewed
    def raises(*args, **kwargs):
        raise RuntimeError("render failed")
    monkeypatch.setattr(pm, "edit_review_text", raises, raising=False)
    assert plugin.pre_tool_call(*EDITS[0]) is None


@pytest.mark.parametrize("platform,limit", [("cli", 1000), ("tui", 1000), ("desktop", 1000), ("telegram", 250), ("slack", 250), ("discord", 250), ("mattermost", 1000)])
def test_preview_caps(reviewed, session_env, platform, limit):
    plugin, _ = reviewed
    session_env["HERMES_SESSION_PLATFORM"] = platform
    content = "\n".join(f"preview {n} " + "x" * 100 for n in range(20))
    directive = plugin.pre_tool_call("write_file", {"path": "parser.py", "content": content})
    text = directive["message"]
    assert len(text) <= limit and "preview 0" in text
    if platform in {"cli", "tui", "desktop"}:
        assert len(text.splitlines()) <= 10  # header, eight lines, the omission note
        assert "preview 8" not in text


def test_family_fields_and_linked_copy(reviewed):
    plugin, _ = reviewed
    assert "edit_review" in pm._FAMILY_FIELDS and "edit_review" not in pm._EXECUTION_FIELDS
    current = state(plugin)
    storage = pm._state_storage_key("sk:unit-session")
    linked = pm._state_storage_key("sk:linked-test")
    current["command_session_storage_keys"] = [linked]
    plugin._save_storage_state(linked, {"active": False})
    plugin._save_state("sk:unit-session", current)
    plugin._sync_execution_family(storage, current)
    assert plugin._load_storage_state(linked)["edit_review"] == current["edit_review"]
    plugin.ctx.settings["levers"] = "approval_card todo_batch"
    plugin.post_llm_call()
    assert plugin._load_storage_state(linked)["edit_review"] == current["edit_review"]
    plugin.command("done")
    assert "edit_review" not in plugin._load_storage_state(linked)


@pytest.mark.parametrize("child", [True, False])
def test_delegated_child_edits_are_not_reviewed(reviewed, monkeypatch, child):
    plugin, _ = reviewed
    monkeypatch.setattr(pm, "_delegated_child", lambda: child)
    directive = plugin.pre_tool_call(*EDITS[0])
    assert (directive is None) if child else (directive["action"] == "approve")
    key = state(plugin)["edit_review"]["key"]
    plugin.ctx.hooks["post_approval_response"](pattern_key="plugin_rule:" + key, choice="deny")
    assert bool(state(plugin)["edit_review"].get("denied")) is (not child)


def test_review_after_automatic_clear_stays_in_executing_index(reviewed):
    plugin, _ = reviewed
    storage = pm._state_storage_key("sk:unit-session")
    assert storage in plugin._executing_storage_keys()
    plugin.ctx.settings["levers"] = "approval_card todo_batch"
    plugin.post_llm_call(**IDS)
    assert "phase" not in state(plugin) and state(plugin).get("edit_review")
    assert storage in plugin._executing_storage_keys()


def test_no_lever_read_for_tools_outside_review(card, monkeypatch):
    plugin, _, _ = card
    plugin.command("off")
    calls = []
    real = plugin._levers
    monkeypatch.setattr(plugin, "_levers", lambda: calls.append(1) or real())
    assert plugin.pre_tool_call("read_file", {"path": "x"}) is None
    assert plugin.pre_tool_call(*EDITS[0]) is None
    assert calls == []


@pytest.mark.parametrize("patch,count,shown", [
    ("*** Begin Patch\n*** Delete File: old.py\n*** End Patch", "1 delete", "Delete File: old.py"),
    ("*** Begin Patch\n*** Move File: a.py -> b.py\n*** End Patch", "1 move", "Move File: a.py -> b.py"),
])
def test_preview_shows_deletes_and_moves(reviewed, session_env, patch, count, shown):
    plugin, _ = reviewed
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert f"(+0/−0 lines, {count})" in text and text.endswith(" — " + shown)


def test_terminal_platform_is_one_line(reviewed, session_env):
    plugin, _ = reviewed
    session_env["HERMES_SESSION_PLATFORM"] = "terminal"
    text = plugin.pre_tool_call("write_file", {"path": "parser.py", "content": "a\nb"})["message"]
    assert "\n" not in text and text.endswith(" — +a ⏎ +b")


def test_symlink_inside_cwd_shows_the_real_outside_path(reviewed, session_env, tmp_path):
    plugin, _ = reviewed
    cwd, outside = tmp_path / "workspace", tmp_path / "outside"
    cwd.mkdir()
    outside.mkdir()
    (cwd / "link").symlink_to(outside)
    session_env["TERMINAL_CWD"] = str(cwd)
    text = plugin.pre_tool_call("write_file", {"path": str(cwd / "link" / "x.py"), "content": "a"})["message"]
    assert f"write_file {(outside / 'x.py').resolve()} (1 line, 1 path outside the working directory)" in text


def test_unresolvable_target_still_prompts(reviewed, session_env, monkeypatch):
    plugin, _ = reviewed
    real = pm.os.path.realpath
    def bad(path, *args, **kwargs):
        if str(path).startswith("/abs"):
            raise ValueError("embedded null byte")
        return real(path, *args, **kwargs)
    monkeypatch.setattr(pm.os.path, "realpath", bad)
    directive = plugin.pre_tool_call("write_file", {"path": "/abs/x.py", "content": "a"})
    assert directive["action"] == "approve" and "write_file /abs/x.py (1 line, 1 path outside the working directory)" in directive["message"]


@pytest.mark.parametrize("tool,args", [
    ("write_file", {"content": "a"}),
    ("patch", {"mode": "patch", "patch": "*** Begin Patch\n*** Delete File: {path}\n*** End Patch"}),
])
def test_symlink_entry_shows_the_link_and_its_target(reviewed, session_env, tmp_path, tool, args):
    plugin, _ = reviewed
    cwd, outside = tmp_path / "workspace", tmp_path / "outside"
    cwd.mkdir()
    outside.mkdir()
    (outside / "victim.py").write_text("x\n")
    (cwd / "alias.py").symlink_to(outside / "victim.py")
    session_env["TERMINAL_CWD"] = str(cwd)
    path = str(cwd / "alias.py")
    args = {k: v.replace("{path}", path) for k, v in args.items()}
    if tool == "write_file":
        args["path"] = path
    text = plugin.pre_tool_call(tool, args)["message"]
    assert f"{tool} alias.py → {(outside / 'victim.py').resolve()} (" in text


def test_mixed_patch_shows_a_late_delete_first(reviewed, session_env):
    plugin, _ = reviewed
    body = "\n".join(f"+line {i}" for i in range(9))
    patch = f"*** Begin Patch\n*** Update File: a.py\n{body}\n***Delete  File: old.py\n*** End Patch"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert "(+9/−0 lines, 1 delete) — Delete File: old.py ⏎ +line 0" in text and text.endswith("+line 7 ⏎ … 1 more line")


def test_multi_file_patch_keeps_file_boundaries(reviewed, session_env):
    plugin, _ = reviewed
    patch = "*** Begin Patch\n*** Update File: a.py\n-x\n+y\n*** Add File: b.py\n+z\n*** End Patch"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert text.endswith("(+2/−1 lines) — Update File: a.py ⏎ -x ⏎ +y ⏎ Add File: b.py ⏎ +z")
    single = plugin.pre_tool_call("patch", {"mode": "patch", "patch": "*** Update File: a.py\n-x\n+y"})["message"]
    assert single.endswith("(+1/−1 lines) — -x ⏎ +y")


def test_long_removal_leaves_room_for_the_replacement(reviewed, session_env):
    plugin, _ = reviewed
    old = "\n".join(f"old {i}" for i in range(10))
    text = plugin.pre_tool_call("patch", {"path": "a.py", "old_string": old, "new_string": "new 1\nnew 2"})["message"]
    assert "(+2/−10 lines)" in text and text.endswith("-old 5 ⏎ +new 1 ⏎ +new 2 ⏎ … 4 more lines") and "-old 6" not in text


def test_relative_target_through_a_symlink_shows_the_outside_path(reviewed, session_env, tmp_path):
    plugin, _ = reviewed
    cwd, outside = tmp_path / "workspace", tmp_path / "outside"
    cwd.mkdir()
    outside.mkdir()
    (cwd / "link").symlink_to(outside)
    session_env["TERMINAL_CWD"] = str(cwd)
    text = plugin.pre_tool_call("write_file", {"path": "link/x.py", "content": "a"})["message"]
    assert f"write_file {(outside / 'x.py').resolve()} (1 line, 1 path outside the working directory)" in text


@pytest.mark.parametrize("space", ["\v", "\f", "\x85", "\u2028", "\u2029", "\r", " \t "])
def test_operation_lines_split_like_the_host(reviewed, session_env, space):
    plugin, _ = reviewed
    patch = f"*** Begin Patch\r\n***{space}Delete File: victim.py\r\n***{space}Move File: a.py -> b.py\r\n*** End Patch"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert "(+0/−0 lines, 1 delete, 1 move) — Delete File: victim.py ⏎ Move File: a.py -> b.py" in text


def test_every_delete_is_listed_beyond_eight_lines(reviewed, session_env):
    plugin, _ = reviewed
    patch = "\n".join(f"*** Delete File: d{n}.py" for n in range(9)) + "\n*** Update File: a.py\n+x"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert "(+1/−0 lines, 9 deletes)" in text and "Delete File: d8.py ⏎ +x" in text


def test_content_line_breaks_stay_on_one_line(reviewed, session_env):
    plugin, _ = reviewed
    patch = "*** Update File: a.py\n+one\u2028two\x85three\vfour"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert text.endswith(" — +one two three four") and len(text.splitlines()) == 1


def test_operation_paths_are_shown_relative(reviewed, session_env, tmp_path):
    plugin, _ = reviewed
    session_env["TERMINAL_CWD"] = str(tmp_path)
    patch = (f"*** Update File: {tmp_path}/a.py\n+x\n*** Update File: {tmp_path}/b.py\n+y\n"
             f"*** Move File: {tmp_path}/c.py -> {tmp_path}/d.py")
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert text.endswith("— Move File: c.py -> d.py ⏎ Update File: a.py ⏎ +x ⏎ Update File: b.py ⏎ +y")


@pytest.mark.parametrize("split", ["\n", "\n\n  \n", "\r\n   "])
def test_split_move_is_one_move(reviewed, session_env, split):
    plugin, _ = reviewed
    patch = f"*** Begin Patch\n*** Move File: a.py{split}-> b.py\n*** End Patch"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert text.endswith("(+0/−0 lines, 1 move) — Move File: a.py -> b.py")


def test_local_platform_is_one_line(reviewed, session_env):
    plugin, _ = reviewed
    session_env["HERMES_SESSION_PLATFORM"] = "local"
    text = plugin.pre_tool_call("write_file", {"path": "parser.py", "content": "a\nb"})["message"]
    assert "\n" not in text and text.endswith(" — +a ⏎ +b")


@pytest.mark.parametrize("platform,limit", [("cli", 1000), ("telegram", 250)])
def test_long_names_cannot_hide_destructive_counts(reviewed, session_env, tmp_path, platform, limit):
    plugin, _ = reviewed
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    session_env["TERMINAL_CWD"] = str(cwd)
    session_env["HERMES_SESSION_PLATFORM"] = platform
    adds = "\n".join(f"*** Add File: a{n}_" + "x" * 120 + ".py\n+x" for n in range(9))
    patch = f"*** Begin Patch\n{adds}\n*** Delete File: ../victim.py\n*** Move File: s.py -> t.py\n*** End Patch"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert len(text) <= limit and "1 delete, 1 move, 1 path outside the working directory)" in text


@pytest.mark.parametrize("platform,limit", [("cli", 1000), ("telegram", 250)])
def test_home_relative_target_counts_as_outside(reviewed, session_env, tmp_path, platform, limit):
    plugin, _ = reviewed
    session_env["TERMINAL_CWD"] = str(tmp_path)
    session_env["HERMES_SESSION_PLATFORM"] = platform
    deletes = "\n".join(f"*** Delete File: d{n}_" + "x" * 120 + ".py" for n in range(9))
    patch = f"*** Begin Patch\n{deletes}\n*** Delete File: ~/victim.py\n*** End Patch"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert len(text) <= limit and "(+0/−0 lines, 10 deletes, 1 path outside the working directory)" in text


def test_displayed_paths_keep_their_whitespace(reviewed, session_env, tmp_path):
    plugin, _ = reviewed
    session_env["TERMINAL_CWD"] = str(tmp_path)
    patch = "*** Begin Patch\n*** Delete File: a  b.py\n*** Delete File: c\td.py\n*** End Patch"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert "patch a  b.py, c\td.py (+0/−0 lines, 2 deletes) — Delete File: a  b.py ⏎ Delete File: c\td.py" in text


def test_large_patch_preview_is_fast(reviewed, session_env, tmp_path):
    import time
    plugin, _ = reviewed
    session_env["TERMINAL_CWD"] = str(tmp_path)
    patch = "*** Begin Patch\n" + "\n".join(f"*** Delete File: sub/d{n}.py" for n in range(10000)) + "\n*** End Patch"
    start = time.perf_counter()
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert time.perf_counter() - start < 5 and "10000 deletes" in text and len(text) <= 1000


def test_outside_target_is_named_first(reviewed, session_env, tmp_path):
    plugin, _ = reviewed
    cwd = tmp_path / "workspace"
    cwd.mkdir()
    session_env["TERMINAL_CWD"] = str(cwd)
    adds = "\n".join(f"*** Add File: a{n}_" + "x" * 120 + ".py\n+x" for n in range(9))
    patch = f"*** Begin Patch\n{adds}\n*** Add File: ../out.py\n+y\n*** End Patch"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    out = str((tmp_path / "out.py").resolve())
    assert f"patch {out}, a0_" in text and "1 path outside the working directory)" in text


def test_a_long_removed_line_leaves_room_for_the_replacement(reviewed, session_env):
    plugin, _ = reviewed
    text = plugin.pre_tool_call("patch", {"path": "a.py", "old_string": "o" * 2000, "new_string": "new"})["message"]
    assert len(text) <= 1000 and text.endswith("… ⏎ +new")


def test_a_long_move_keeps_its_destination(reviewed, session_env):
    plugin, _ = reviewed
    source = "s" * 200 + ".py"
    patch = f"*** Begin Patch\n*** Move File: {source} -> dest.py\n*** End Patch"
    text = plugin.pre_tool_call("patch", {"mode": "patch", "patch": patch})["message"]
    assert text.endswith(f"Move File: {source} -> dest.py")
