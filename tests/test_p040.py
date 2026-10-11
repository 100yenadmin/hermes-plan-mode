"""P040 wording, approval-directive compatibility and plan naming regressions."""
import hashlib
from pathlib import Path
import re

import pytest

from plan_mode import plugin as pm
from plan_mode.approval import _title, approval_text
from test_levers import make_plugin, write_plan
from test_plan_mode import plan_profile, session_env
from test_plan_once import capture, once, submit


CAUSES = "(yolo, approvals.mode: off, or an unattended surface such as the API server or a webhook)"


@pytest.mark.parametrize("presented", [False, True])
def test_p040_bypass_names_unattended_surfaces(make_plugin, monkeypatch, presented, plan_profile):
    plugin = make_plugin(plan_profile=plan_profile)
    monkeypatch.setattr(pm.secrets, "token_hex", lambda size: "fixednonce")
    plugin._save_command_state = lambda *args: None
    state = {"active": True, "activation_id": "fixed", "submission": {
        "path": "/plans/fixed.md", "revision": 3, "rule_key": "plan-mode:fixed",
        "digest": "digest", "tool_call_id": "submit", "status": "pending"}}
    plugin._ledger.mark_inflight("plan-mode:fixed", "submit")
    if presented:
        plugin._ledger.record_presented(pattern_key="plugin_rule:plan-mode:fixed")
    result = plugin._complete_submission("raw", "storage", state)
    assert CAUSES in result["message"]
    assert state["active"] and state["submission"]["status"] == "awaiting"
    assert "approved" not in result
    if presented:
        assert result["message"] == (
            "No human approval was recorded for plan rev 3: this host approved the call "
            f"automatically {CAUSES} or does not route plugin approvals. "
            "Plan mode stays on. Show the plan and ask the user to run /planmode approve "
            "(or /planmode reject <feedback>).")
    else:
        assert "clarify" in result


def test_p040_submit_disallows_permanent_approval(make_plugin, plan_profile):
    plugin = make_plugin(plan_profile=plan_profile)
    path = write_plan(plugin)
    directive = plugin.pre_tool_call("plan_mode", {"action": "submit", "path": str(path)},
                                     tool_call_id="submit")
    assert directive["action"] == "approve"
    assert directive["allow_permanent"] is False


@pytest.mark.parametrize("platform", ["cli", "terminal"])
@pytest.mark.parametrize("title", ["Add divide", "x" * 200])
def test_p040_cli_prompt_is_honest_and_one_line(platform, title):
    result = approval_text(f"# {title}\n\nDo it.", "/plans/p.md", 2, platform)
    assert result == (
        f"Plan rev 2 (p.md): {title[:160]}. Approve to start implementing, deny to keep planning "
        "(the plan is in the reply above; if it is not, deny and run /planmode show).")
    assert "\n" not in result


TITLE_CASES = [
    ("# Summary\n\n**Add** `slugify` to parser.py.", "Add slugify to parser.py."),
    ("# Goal\nFirst body.\n## Add slugify", "Add slugify"),
    ("# Existing title\n## Summary\nBody.", "Existing title"),
    ("# Summary\n## Goal\n## Plan\n", ""),
    ("# Test endpoint\n## Validation", "Test endpoint"),
    ("# Tests / validation\n## Notes\n[Add slugify](https://example.test)", "Add slugify"),
    ("# Plan: Add power\n", "Add power"),
    ("# Plan — Add power\n", "Add power"),
    ("# Summary\n" + "x" * 120, "x" * 80),
    ("# Summary\n```bash\n# run tests\n```\nAdd slugify.", "Add slugify."),
    ("## Summary\n- **Add** slugify to textutil\n", "Add slugify to textutil"),
    ("```python\n# not a heading\n```\n# Real title\n", "Real title"),
    ("## Summary\nAdd slugify to parser.\n## Key changes\n- parser.py\n## Test plan\n- pytest\n## Assumptions\n- none",
     "Add slugify to parser."),
    ("## Goal\nAdd slugify.\n## Decisions and assumptions\n- ascii\n## Changes\n- parser.py\n## Steps\n1. Edit\n"
     "## Validation\n- pytest", "Add slugify."),
    ("## Key changes\n## Real title", "Real title"),
]


def test_p040_title_without_headings_keeps_file_name():
    # 0.3.5 behaviour: a plan with no headings is titled by its file name, never by a step line.
    assert _title("first line\nsecond line", "", "fallback.md") == "fallback.md"
    assert _title("1. Change parser.py\n2. Run tests", "", "fallback.md") == "fallback.md"


@pytest.mark.parametrize("text,chosen", TITLE_CASES)
def test_p040_title_skips_generic_headings(text, chosen):
    assert _title(text, "", "fallback.md") == (chosen or "fallback.md")
    assert _title(text, "  Plan: **Chosen summary**\n", "fallback.md") == "Chosen summary"


@pytest.mark.parametrize("text,chosen", TITLE_CASES)
def test_p040_once_slug_uses_same_chosen_text(once, text, chosen):
    plugin, _ = once
    # Meet the existing capture minimum without adding a title or body fallback.
    text += "\n" + "## Summary\n" * 10
    capture(plugin, text)
    directive = submit(plugin)
    assert directive["action"] == "approve"
    path = Path(plugin._load_state("ui:ui-1")["submission"]["path"])
    slug = re.sub(r"[^a-z0-9]+", "-", chosen.lower()).strip("-")[:60]
    slug = slug or hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{6}-(.+)-[a-f0-9]{12}\.md", path.name)[1] == slug
    assert _title(path.read_text(), "", path.name) == (chosen or path.name)
